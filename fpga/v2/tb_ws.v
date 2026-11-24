`timescale 1ns/1ps
module tb;
    reg clk = 0; always #18.518 clk = ~clk;      // 27 MHz
    reg rx = 1;
    wire wsd, tx, sck, ws, pr, pg, pb, l0, l1;
    reg sd = 0;
    ledctl_top dut (.clk(clk), .uart_rx(rx), .uart_tx(tx), .i2s_sck(sck), .i2s_ws(ws), .i2s_sd(sd),
                    .pwm_r(pr), .pwm_g(pg), .pwm_b(pb), .led0_n(l0), .led1_n(l1), .ws_direct(wsd));
    task sendb(input [7:0] b); integer i; begin
        rx = 0; #1000; for (i = 0; i < 8; i = i + 1) begin rx = b[i]; #1000; end rx = 1; #1000;
    end endtask
    // decode the strip line (= ~pwm_r)
    wire line = wsd;
    real t_rise; integer nbits = 0; reg [23:0] word = 0; integer leds = 0;
    integer dts = 0; always @(dut.u_ws.dout) dts = dts + 1; always @(dut.u_ws.st) if (dut.u_ws.st == 1 && dut.u_ws.led == 0 && dut.u_ws.col == 0) begin $display("%0t engine start live=%0d", $time, dut.live); nbits = 0; leds = 0; end
    always @(posedge line) t_rise = $realtime;
    always @(negedge line) begin
        word = {word[22:0], ($realtime - t_rise) > 600.0};
        nbits = nbits + 1;
        if (nbits % 24 == 0) begin $display("LED %0d: G=%02h R=%02h B=%02h  (high %0.0f ns)", leds, word[23:16], word[15:8], word[7:0], $realtime - t_rise); leds = leds + 1; end
    end
    initial begin
        #200000;
        // LED count = 3
        sendb(8'hA5); sendb("N"); sendb(8'd3); sendb(8'd0); sendb(8'd0);
        // frame: 3 LEDs, includes 0xA5/0xA7 bytes inside the data on purpose
        sendb(8'hA7); sendb(8'd3); sendb(8'd0);
        sendb(8'h80); sendb(8'h01); sendb(8'hFF);
        sendb(8'hA5); sendb(8'hA7); sendb(8'h00);
        sendb(8'h12); sendb(8'h34); sendb(8'h56);
        #20; $display("fstate=%0d wleft=%0d since=%0d live=%0d st=%0d pend=%0d", dut.fstate, dut.wleft, dut.since_frame, dut.live, dut.u_ws.st, dut.u_ws.pend);
        #500000;
        $display("ws_mode=%0d nleds=%0d nbits=%0d dout_toggles=%0d", dut.ws_mode, dut.nleds, nbits, dts);
        $finish;
    end
endmodule
module mon;
endmodule
