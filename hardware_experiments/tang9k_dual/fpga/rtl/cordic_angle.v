// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL vectoring CORDIC.
// Angle of (x, y) in 2^-16 turns, bit-exact with mrc_model.cordic_angle:
// a 180-degree pre-rotation for x < 0, then N shift-add steps (arithmetic
// shifts). Fully pipelined: one result per clock, latency N + 1.
`default_nettype none
module cordic_angle #(
    parameter W = 18,             // input width; |x|,|y| < 2^(W-3) keeps the gain growth inside
    parameter N = 10              // steps (mrc_model.CORDIC_ITERS)
) (
    input  wire                clk,
    input  wire signed [W-1:0] x_in,
    input  wire signed [W-1:0] y_in,
    output wire        [15:0]  ang
);
`include "tables.vh"
    reg signed [W-1:0] xs [0:N];
    reg signed [W-1:0] ys [0:N];
    reg        [15:0]  as [0:N];

    always @(posedge clk) begin
        if (x_in < 0) begin
            xs[0] <= -x_in;
            ys[0] <= -y_in;
            as[0] <= 16'd32768;
        end else begin
            xs[0] <= x_in;
            ys[0] <= y_in;
            as[0] <= 16'd0;
        end
    end

    genvar i;
    generate
        for (i = 0; i < N; i = i + 1) begin : step
            wire pos = ys[i] > 0;
            wire signed [W-1:0] ysh = ys[i] >>> i, xsh = xs[i] >>> i;
            // x +/- y as one adder with carry-in: {x,1} + {y or ~y, sub}
            wire [W:0] xn = {xs[i], 1'b1} + {(pos ? ysh : ~ysh), ~pos};
            wire [W:0] yn = {ys[i], 1'b1} + {(pos ? ~xsh : xsh), pos};
            always @(posedge clk) begin
                xs[i+1] <= xn[W:1];
                ys[i+1] <= yn[W:1];
                as[i+1] <= as[i] + (pos ? atan_lut(i) : -atan_lut(i));
            end
        end
    endgenerate

    assign ang = as[N];
endmodule
`default_nettype wire
