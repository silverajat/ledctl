// ledctl v1 FPGA: music-sync LED controller (GW2AR-LV18QN88C8/I7, 27 MHz)
//
//  ESP32 --UART 115200--> cmd parser --> base colour (r,g,b), mode
//  ICS-43434 --I2S--> 24-bit samples (~26.4 kHz) --> beat detector (M1 model, fixed point)
//  mode 0 STATIC : strip = base colour
//  mode 1 MUSIC  : strip = base colour x (floor + loudness + beat flash)
//  mode 2 CHILL  : strip = base colour x (floor + slow loudness), no flash
//  telemetry --> ESP32 every 10 ms + on beats: 0xFF, loud, level, beat count
//  LED0 = beat indicator, LED1 = heartbeat
//
//  UART frame (ESP32 -> FPGA): 0xA5, cmd, a, b, c
//     cmd 'C' : base colour r=a g=b b=c (0-255, ESP32 applies the power cap)
//     cmd 'M' : mode = a
//     cmd 'T' : beat threshold ratio in 1/8 steps = a (16 = 2.0x), floor = b
module ledctl_top (
    input  wire clk,        // pin 4, 27 MHz
    input  wire uart_rx,    // pin 70, from ESP32 GPIO4
    output wire uart_tx,    // pin 69, to ESP32 GPIO5
    output wire i2s_sck,    // pin 82
    output wire i2s_ws,     // pin 81
    input  wire i2s_sd,     // pin 80
    output wire pwm_r,      // pin 71
    output wire pwm_g,      // pin 72
    output wire pwm_b,      // pin 73
    output wire led0_n,     // pin 74
    output wire led1_n      // pin 75
);
    // ---------------- reset ----------------
    reg [7:0] por = 0;
    wire rst = ~por[7];
    always @(posedge clk) if (!por[7]) por <= por + 1'b1;

    // ---------------- UART RX + command parser ----------------
    wire [7:0] rx_byte; wire rx_valid;
    uart_rx #(.DIV(234)) u_rx (.clk(clk), .rx(uart_rx), .data(rx_byte), .valid(rx_valid)); // 27e6/115200 = 234.4

    reg [7:0] base_r = 8'd40, base_g = 8'd10, base_b = 8'd60;
    reg [1:0] mode = 2'd1;
    reg [7:0] thr_ratio = 8'd16, thr_floor = 8'd8;
    reg [2:0] fstate = 0; reg [7:0] fcmd, fa, fb;
    always @(posedge clk) begin
        if (rst) fstate <= 0;
        else if (rx_valid) begin
            case (fstate)
                0: if (rx_byte == 8'hA5) fstate <= 1;
                1: begin fcmd <= rx_byte; fstate <= 2; end
                2: begin fa <= rx_byte; fstate <= 3; end
                3: begin fb <= rx_byte; fstate <= 4; end
                4: begin
                    fstate <= 0;
                    case (fcmd)
                        "C": begin base_r <= fa; base_g <= fb; base_b <= rx_byte; end
                        "M": mode <= fa[1:0];
                        "T": begin thr_ratio <= fa; thr_floor <= fb; end
                        default: ;
                    endcase
                end
                default: fstate <= 0;
            endcase
        end
    end

    // ---------------- I2S mic ----------------
    wire signed [23:0] sample; wire sample_valid;
    i2s_rx u_i2s (.clk(clk), .rst(rst), .sck(i2s_sck), .ws(i2s_ws), .sd(i2s_sd), .sample(sample), .valid(sample_valid));

    // ---------------- beat detector ----------------
    wire beat; wire [7:0] loud, loud_slow, level;
    beat_detect u_bd (.clk(clk), .rst(rst), .x(sample), .x_valid(sample_valid),
                      .ratio8(thr_ratio), .floor_in(thr_floor), .beat(beat), .loud(loud), .loud_slow(loud_slow),
                      .level(level));

    // ---------------- 3-band spectrum (bass / mid / high) ----------------
    wire [7:0] lv_bass, lv_mid, lv_high;
    bands u_bands (.clk(clk), .rst(rst), .x(sample), .x_valid(sample_valid),
                   .bass(lv_bass), .mid(lv_mid), .high(lv_high));

    // ---------------- 1 kHz tick ----------------
    reg [14:0] ms_cnt = 0; wire ms_tick = (ms_cnt == 15'd26999);
    always @(posedge clk) ms_cnt <= ms_tick ? 15'd0 : ms_cnt + 1'b1;

    // smoothed loudness for chill mode (~256 ms)
    reg [15:0] lsm = 0;
    always @(posedge clk) if (ms_tick) lsm <= lsm + loud - (lsm >> 8);

    // beat flash: jumps to 255, decays ~ 1/32 per 8 ms (~ 250 ms to fade)
    reg [7:0] flash = 0; reg [2:0] ms8 = 0;
    always @(posedge clk) begin
        if (beat && mode == 2'd1) flash <= 8'd255;
        else if (ms_tick) begin
            ms8 <= ms8 + 1'b1;
            if (ms8 == 0) flash <= flash - (flash >> 3) - (flash != 0);
        end
    end

    // intensity 0..255
    reg [9:0] inten;
    always @(posedge clk) begin
        case (mode)
            2'd0: inten <= 10'd255;
            2'd1: inten <= 10'd38 + (loud >> 1) + (flash >> 1) + (flash >> 2);
            2'd2: inten <= 10'd40 + lsm[15:8];
            default: inten <= 10'd0;      // mode 3 = off
        endcase
    end
    wire [7:0] k = (inten > 10'd255) ? 8'd255 : inten[7:0];

    // scaled colour = base * k / 256 (k=255 ~ full)
    reg [7:0] out_r, out_g, out_b;
    always @(posedge clk) begin
        out_r <= ({8'd0, base_r} * {8'd0, k}) >> 8;
        out_g <= ({8'd0, base_g} * {8'd0, k}) >> 8;
        out_b <= ({8'd0, base_b} * {8'd0, k}) >> 8;
    end

    // ---------------- PWM ~26 kHz (27 MHz / 4 / 256) ----------------
    reg [1:0] pdiv = 0; reg [7:0] pcnt = 0;
    always @(posedge clk) begin pdiv <= pdiv + 1'b1; if (pdiv == 0) pcnt <= pcnt + 1'b1; end
    assign pwm_r = (pcnt < out_r);
    assign pwm_g = (pcnt < out_g);
    assign pwm_b = (pcnt < out_b);

    // ---------------- telemetry -> ESP32 ----------------
    // every 10 ms and immediately on a beat: 0xFF, loud, level, beat count, bass, mid, high (all 0-254)
    reg [7:0] beat_cnt = 0; reg pend = 0; reg [3:0] t10 = 0;
    reg [2:0] tx_idx = 0; reg tx_go = 0; reg [7:0] tx_data = 0; reg sending = 0;
    reg [7:0] snap_loud = 0, snap_lvl = 0, snap_cnt = 0, snap_b = 0, snap_m = 0, snap_h = 0;
    wire tx_busy;
    always @(posedge clk) begin
        tx_go <= 1'b0;
        if (beat) beat_cnt <= (beat_cnt >= 8'd253) ? 8'd0 : beat_cnt + 1'b1;
        if (ms_tick) t10 <= (t10 == 4'd9) ? 4'd0 : t10 + 1'b1;
        if (beat || (ms_tick && t10 == 4'd9)) pend <= 1'b1;
        if (!sending) begin
            if (pend && !beat) begin
                pend <= 1'b0; sending <= 1'b1; tx_idx <= 0;
                snap_loud <= (loud == 8'hFF) ? 8'hFE : loud;
                snap_lvl <= (level == 8'hFF) ? 8'hFE : level;
                snap_cnt <= beat_cnt;
                snap_b <= (lv_bass == 8'hFF) ? 8'hFE : lv_bass;
                snap_m <= (lv_mid == 8'hFF) ? 8'hFE : lv_mid;
                snap_h <= (lv_high == 8'hFF) ? 8'hFE : lv_high;
            end
        end else if (!tx_busy && !tx_go) begin
            case (tx_idx)
                3'd0: tx_data <= 8'hFF;
                3'd1: tx_data <= snap_loud;
                3'd2: tx_data <= snap_lvl;
                3'd3: tx_data <= snap_cnt;
                3'd4: tx_data <= snap_b;
                3'd5: tx_data <= snap_m;
                default: tx_data <= snap_h;
            endcase
            tx_go <= 1'b1;
            tx_idx <= tx_idx + 1'b1;
            if (tx_idx == 3'd6) sending <= 1'b0;
        end
    end
    uart_tx #(.DIV(234)) u_tx (.clk(clk), .data(tx_data), .start(tx_go), .tx(uart_tx), .busy(tx_busy));

    // ---------------- LEDs ----------------
    reg [7:0] beat_led = 0;
    always @(posedge clk) if (beat) beat_led <= 8'd80; else if (ms_tick && beat_led != 0) beat_led <= beat_led - 1'b1;
    assign led0_n = (beat_led == 0);
    reg [24:0] hb = 0; always @(posedge clk) hb <= hb + 1'b1;
    assign led1_n = hb[24];
endmodule

// ------------------------------------------------------------------
module uart_rx #(parameter DIV = 234) (input wire clk, input wire rx, output reg [7:0] data, output reg valid);
    reg [1:0] sync = 2'b11; always @(posedge clk) sync <= {sync[0], rx};
    wire r = sync[1];
    reg [8:0] cnt = 0; reg [3:0] bitn = 0; reg busy = 0; reg [7:0] sh = 0;
    always @(posedge clk) begin
        valid <= 1'b0;
        if (!busy) begin
            if (!r) begin busy <= 1; cnt <= DIV / 2; bitn <= 0; end
        end else if (cnt == 0) begin
            cnt <= DIV - 1;
            if (bitn == 0) begin if (r) busy <= 0; else bitn <= 1; end          // start bit check
            else if (bitn <= 8) begin sh <= {r, sh[7:1]}; bitn <= bitn + 1'b1; end
            else begin busy <= 0; if (r) begin data <= sh; valid <= 1'b1; end end // stop bit
        end else cnt <= cnt - 1'b1;
    end
endmodule

module uart_tx #(parameter DIV = 234) (input wire clk, input wire [7:0] data, input wire start, output reg tx = 1'b1, output reg busy = 1'b0);
    reg [8:0] cnt = 0; reg [3:0] bitn = 0; reg [9:0] sh = 10'h3FF;
    always @(posedge clk) begin
        if (!busy) begin
            tx <= 1'b1;
            if (start) begin busy <= 1; sh <= {1'b1, data, 1'b0}; cnt <= DIV - 1; bitn <= 0; tx <= 1'b0; end
        end else if (cnt == 0) begin
            cnt <= DIV - 1;
            if (bitn == 9) busy <= 0;
            else begin bitn <= bitn + 1'b1; sh <= {1'b1, sh[9:1]}; tx <= sh[1]; end
        end else cnt <= cnt - 1'b1;
    end
endmodule

// I2S master for the ICS-43434 (L/R tied low = left slot). SCK = 27 MHz / 16 = 1.6875 MHz, 64 SCK per frame
// -> fs = 26.37 kHz. The mic changes SD on SCK falling edges; we sample on rising edges.
module i2s_rx (input wire clk, input wire rst, output reg sck = 0, output reg ws = 0, input wire sd,
               output reg signed [23:0] sample = 0, output reg valid = 0);
    reg [2:0] div = 0; reg [5:0] bitc = 0; reg [23:0] sh = 0; reg sd_s = 0;
    always @(posedge clk) begin
        valid <= 1'b0;
        sd_s <= sd;
        div <= div + 1'b1;
        if (div == 3'd7) begin
            sck <= ~sck;
            if (sck) begin                         // falling edge of SCK: advance bit counter, update WS
                bitc <= bitc + 1'b1;
                ws <= (bitc + 1'b1) >= 6'd32;      // WS low for left slot (bits 0-31), high for right
            end else begin                         // rising edge: sample SD
                // left slot data: MSB arrives on the 2nd SCK of the slot (I2S one-bit delay) -> bits 1..24
                if (bitc >= 6'd1 && bitc <= 6'd24) sh <= {sh[22:0], sd_s};
                if (bitc == 6'd25) begin sample <= sh; valid <= 1'b1; end
            end
        end
    end
endmodule

// Beat detector = M1 Python model in fixed point (shift-only one-pole filters), ~26.4 kHz sample rate.
//  bass = LP(~131 Hz) - LP(~33 Hz); rect = |bass|
//  fast: attack ~4.8 ms, release ~39 ms; slow: ~620 ms
//  beat when fast > ratio * slow and fast > floor, then 220 ms hold-off; re-arm when fast < 0.85 * ratio * slow
module beat_detect (input wire clk, input wire rst, input wire signed [23:0] x, input wire x_valid,
                    input wire [7:0] ratio8, input wire [7:0] floor_in,
                    output reg beat = 0, output reg [7:0] loud = 0, output reg [7:0] loud_slow = 0,
                    output reg [7:0] level = 0);
    // Q.16 fixed point accumulators, 48-bit signed
    reg signed [47:0] lp1 = 0, lp2 = 0;
    reg [47:0] fast = 0, slow = 0, lfast = 0, lslow = 0;
    wire signed [47:0] xin = {{8{x[23]}}, x, 16'd0};
    wire signed [47:0] band = lp1 - lp2;
    wire [47:0] rect = band[47] ? -band : band;
    wire [47:0] ax = x[23] ? -xin : xin;                     // |x|
    reg [15:0] hold = 0; reg armed = 1; reg [15:0] warm = 0;
    // ratio8 in 1/8: thr = slow * ratio8 / 8
    wire [55:0] thr = (slow * ratio8) >> 3;
    wire [55:0] rearm = (thr * 7) >> 3;                      // ~0.875 x thr
    wire [47:0] floor_q = {24'd0, floor_in, 16'd0};          // floor in 24-bit sample units
    always @(posedge clk) begin
        beat <= 1'b0;
        if (rst) begin lp1 <= 0; lp2 <= 0; fast <= 0; slow <= 0; hold <= 0; armed <= 1; warm <= 0; end
        else if (x_valid) begin
            lp1 <= lp1 + ((xin - lp1) >>> 5);
            lp2 <= lp2 + ((lp1 - lp2) >>> 7);
            if (rect > fast) fast <= fast + ((rect - fast) >> 7);
            else             fast <= fast - ((fast - rect) >> 10);
            slow <= slow + (rect >> 14) - (slow >> 14);
            if (hold != 0) hold <= hold - 1'b1;
            if (warm != 16'hFFFF) warm <= warm + 1'b1;
            if (warm > 16'd40000 && armed && hold == 0 && {8'd0, fast} > thr && fast > floor_q) begin
                beat <= 1'b1; armed <= 1'b0; hold <= 16'd5800;     // 220 ms at 26.37 kHz
            end else if (!armed && {8'd0, fast} < rearm) armed <= 1'b1;
            // loudness for brightness: full-band |x| fast (~10 ms) and slow (~2.5 s), auto-gained by the slow level
            if (ax > lfast) lfast <= lfast + ((ax - lfast) >> 8);   // (unsigned-safe one-pole)
            else            lfast <= lfast - ((lfast - ax) >> 8);
            lslow <= lslow + (lfast >> 16) - (lslow >> 16);
        end
    end
    // normalise: loud = lfast / lslow * 128 approx, using leading-one of lslow (cheap divide)
    function [5:0] msb; input [47:0] v; integer i; begin msb = 0; for (i = 0; i < 48; i = i + 1) if (v[i]) msb = i[5:0]; end endfunction
    wire [5:0] m = msb(lslow);
    wire [47:0] ln = (m >= 6'd7) ? (lfast >> (m - 6'd7)) : (lfast << (6'd7 - m));
    wire [47:0] lsn = (m >= 6'd7) ? (lslow >> (m - 6'd7)) : (lslow << (6'd7 - m));
    // absolute level: log2(lfast) in 1/4 steps (0 = silence / no mic)
    wire [5:0] mf = msb(lfast);
    wire [47:0] lfn = (mf >= 6'd2) ? (lfast >> (mf - 6'd2)) : (lfast << (6'd2 - mf));
    always @(posedge clk) begin
        level <= (lfast == 0) ? 8'd0 : {mf, lfn[1:0]};
        loud <= (ln > 48'd255) ? 8'd255 : ln[7:0];
        loud_slow <= (lsn > 48'd255) ? 8'd255 : lsn[7:0];
    end
endmodule

// 3-band envelope follower at ~26.4 kHz, outputs log2 levels in 1/4 steps (0 = silence)
//  bass: 2nd-order LP ~130 Hz
//  mid : 2nd-order LP ~1.1 kHz minus bass
//  high: input minus 2nd-order LP ~1.1 kHz
//  envelope: attack ~2.4 ms, release ~19 ms
module bands (input wire clk, input wire rst, input wire signed [23:0] x, input wire x_valid,
              output reg [7:0] bass = 0, output reg [7:0] mid = 0, output reg [7:0] high = 0);
    reg signed [47:0] lpb = 0, lpb2 = 0, lpm = 0, lpm2 = 0;   // 2nd-order (two cascaded one-poles) low-passes
    wire signed [47:0] xin = {{8{x[23]}}, x, 16'd0};
    wire signed [47:0] sb = lpb2, sm = lpm2 - lpb2, sh = xin - lpm2;
    wire [47:0] ab = sb[47] ? -sb : sb, am = sm[47] ? -sm : sm, ah = sh[47] ? -sh : sh;
    reg [47:0] eb = 0, em = 0, eh = 0;
    always @(posedge clk) begin
        if (rst) begin lpb <= 0; lpb2 <= 0; lpm <= 0; lpm2 <= 0; eb <= 0; em <= 0; eh <= 0; end
        else if (x_valid) begin
            lpb  <= lpb  + ((xin - lpb)  >>> 5);    // ~130 Hz
            lpb2 <= lpb2 + ((lpb - lpb2) >>> 5);
            lpm  <= lpm  + ((xin - lpm)  >>> 2);    // ~1.1 kHz
            lpm2 <= lpm2 + ((lpm - lpm2) >>> 2);
            if (ab > eb) eb <= eb + ((ab - eb) >> 6); else eb <= eb - ((eb - ab) >> 9);
            if (am > em) em <= em + ((am - em) >> 6); else em <= em - ((em - am) >> 9);
            if (ah > eh) eh <= eh + ((ah - eh) >> 6); else eh <= eh - ((eh - ah) >> 9);
        end
    end
    function [5:0] msb; input [47:0] v; integer i; begin msb = 0; for (i = 0; i < 48; i = i + 1) if (v[i]) msb = i[5:0]; end endfunction
    function [7:0] lg; input [47:0] v; reg [5:0] m; reg [47:0] n; begin
        m = msb(v);
        n = (m >= 6'd2) ? (v >> (m - 6'd2)) : (v << (6'd2 - m));
        lg = (v == 0) ? 8'd0 : {m, n[1:0]};
    end endfunction
    always @(posedge clk) begin
        bass <= lg(eb); mid <= lg(em); high <= lg(eh);
    end
endmodule
