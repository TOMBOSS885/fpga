module TopModule(input signed [7:0] a, input signed [7:0] b, output signed [7:0] y);
wire signed [8:0] sum; assign sum={a[7],a}+{b[7],b}; assign y=sum>9'sd127 ? 8'sd127 : sum < -9'sd128 ? 8'h80 : sum[7:0];
endmodule
