`timescale 1ns/1ps
module tb_top;
  reg clk=0; always #18.5 clk=~clk;
  wire tx; reg sd=0;
  ledctl_top dut(.clk(clk),.uart_rx(1'b1),.uart_tx(tx),.i2s_sck(),.i2s_ws(),.i2s_sd(sd),.pwm_r(),.pwm_g(),.pwm_b(),.led0_n(),.led1_n());
  always @(posedge dut.u_i2s.sck) sd <= $random;
  // uart decoder
  integer i; reg [7:0] b;
  initial begin
    #1000;
    forever begin
      @(negedge tx); #(8680/2);
      for (i=0;i<8;i=i+1) begin #8680; b[i]=tx; end
      #8680; $display("%0t ms byte %02x", $time/1000000, b);
    end
  end
  initial begin #35000000; $finish; end
endmodule
