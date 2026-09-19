module top(input logic clk, input logic rst, output logic [7:0] count);
    always_ff @(posedge clk) begin
        if (rst) count <= 0;
        else count <= count + 1;
    end
endmodule
