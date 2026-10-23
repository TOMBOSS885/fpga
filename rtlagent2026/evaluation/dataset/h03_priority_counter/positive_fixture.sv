module TopModule(input clk, input reset, input load, input [3:0] value, input up, input down, output reg [3:0] y);
always @(posedge clk) if(reset) y<=0; else if(load) y<=value; else if(up && !down && y!=15) y<=y+1'b1; else if(down && !up && y!=0) y<=y-1'b1;
endmodule
