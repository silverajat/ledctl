`timescale 1ns/1ps
module tb;
    reg clk = 0; always #18.518 clk = ~clk;
    reg rx = 1;
    wire wsd, tx, sck, ws, pr, pg, pb, l0, l1; reg sd = 0;
    ledctl_top dut (.clk(clk), .uart_rx(rx), .uart_tx(tx), .i2s_sck(sck), .i2s_ws(ws), .i2s_sd(sd),
                    .pwm_r(pr), .pwm_g(pg), .pwm_b(pb), .led0_n(l0), .led1_n(l1), .ws_direct(wsd));
    task sendb(input [7:0] b); integer i; begin
        rx = 0; #1000; for (i = 0; i < 8; i = i + 1) begin rx = b[i]; #1000; end rx = 1; #1000;
    end endtask
    task cmd(input [7:0] c, input [7:0] a, input [7:0] b, input [7:0] d); begin sendb(8'hA5); sendb(c); sendb(a); sendb(b); sendb(d); end endtask
    wire line = wsd;
    real t_rise; integer nbits = 0; reg [23:0] word = 0; integer leds = 0;
    always @(posedge line) t_rise = $realtime;
    always @(negedge line) begin
        word = {word[22:0], ($realtime - t_rise) > 600.0};
        nbits = nbits + 1;
        if (nbits % 24 == 0 && leds < 2) begin $display("%0t LED %0d: G=%02h R=%02h B=%02h", $time, leds, word[23:16], word[15:8], word[7:0]); end
        if (nbits % 24 == 0) leds = leds + 1;
    end
    always @(dut.u_ws.st) if (dut.u_ws.st == 1 && dut.u_ws.led == 0 && dut.u_ws.col == 0) begin nbits = 0; leds = 0; end
    initial begin
        #100000;
        cmd("N", 8'd3, 0, 0); cmd("A", 1, 0, 0); cmd("W", 11, 22, 34); cmd("M", 0, 0, 0); cmd("T", 16, 8, 0); cmd("C", 62, 41, 18);
        $display("after cmds: base=%0d,%0d,%0d mode=%0d n=%0d t=%0d/%0d/%0d", dut.base_r, dut.base_g, dut.base_b, dut.mode, dut.nleds, dut.t0h, dut.t1h, dut.tbit);
        #3000000;
        sendb(8'hA7); sendb(8'd3); sendb(8'd0);
        repeat (3) begin sendb(8'h80); sendb(8'h01); sendb(8'hFF); end
        #400000;
        $finish;
    end
endmodule
