`timescale 1ns/1ps
// stars on J4's spare outputs: "S" g b sets their PWM while frames are live
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
    integer hb = 0, hg = 0, n = 0;
    task duty; begin hb = 0; hg = 0; for (n = 0; n < 4096; n = n + 1) begin @(posedge clk); hb = hb + pb; hg = hg + pg; end end endtask
    initial begin
        #200000;
        duty; $display("before:        B duty %0d/4096  G duty %0d/4096", hb, hg);
        sendb(8'hA5); sendb("S"); sendb(8'd64); sendb(8'd192); sendb(8'd0);       // G=64, B=192
        duty; $display("S, no frames:  B duty %0d/4096  G duty %0d/4096 (off: laptop not sending)", hb, hg);
        sendb(8'hA7); sendb(8'd1); sendb(8'd0); sendb(8'h10); sendb(8'h20); sendb(8'h30);   // one frame -> live
        #1000;
        duty; $display("S + frames:    B duty %0d/4096  G duty %0d/4096 (expect ~3072 / ~1024)", hb, hg);
        $finish;
    end
endmodule
