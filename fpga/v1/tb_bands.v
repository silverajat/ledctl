`timescale 1ns/1ps
module tb_bands;
  reg clk=0; always #18.5 clk=~clk;
  reg signed [23:0] x=0; reg v=0; wire [7:0] b,m,h;
  bands dut(.clk(clk),.rst(1'b0),.x(x),.x_valid(v),.bass(b),.mid(m),.high(h));
  real f; integer n, k;
  initial begin
    for (k=0;k<4;k=k+1) begin
      f = (k==0)?60.0:(k==1)?500.0:(k==2)?5000.0:0.0;
      for (n=0;n<8000;n=n+1) begin
        x = (k==3)?0:$rtoi(2000000.0*$sin(2*3.14159265*f*n/26367.0));
        @(posedge clk); v=1; @(posedge clk); v=0;
      end
      $display("f=%0d Hz  bass=%0d mid=%0d high=%0d", $rtoi(f), b, m, h);
    end
    $finish;
  end
endmodule
