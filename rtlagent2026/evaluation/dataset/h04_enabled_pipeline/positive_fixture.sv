module TopModule(input clk, input reset, input en, input [7:0] din, output reg [7:0] y);
reg [7:0] stage1; always @(posedge clk) if(reset) begin stage1<=0; y<=0; end else if(en) begin stage1<=din; y<=stage1; end
endmodule
