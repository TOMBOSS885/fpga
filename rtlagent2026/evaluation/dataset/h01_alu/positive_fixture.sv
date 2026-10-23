module TopModule(input [7:0] a, input [7:0] b, input [1:0] sel, output [7:0] y);
assign y=sel==0 ? a^b : sel==1 ? a&b : sel==2 ? {a[6:0],a[7]} : a>=b ? a:b;
endmodule
