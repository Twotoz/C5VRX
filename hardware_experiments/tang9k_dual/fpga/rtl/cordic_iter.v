// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL iterative
// vectoring CORDIC (one shift-add step per clock), for the slow phase
// tracker angle. Same arithmetic as cordic_angle.v / mrc_model.cordic_angle:
// start latches (x, y); done pulses N + 1 clocks later with ang valid.
`default_nettype none
module cordic_iter #(
    parameter W = 20,
    parameter N = 10
) (
    input  wire                clk,
    input  wire                start,
    input  wire signed [W-1:0] x_in,
    input  wire signed [W-1:0] y_in,
    output reg         [15:0]  ang,
    output reg                 done
);
`include "tables.vh"
    reg signed [W-1:0] x, y;
    reg        [4:0]   i;
    reg                busy = 1'b0;
    wire signed [W-1:0] ysh = y >>> i, xsh = x >>> i;
    wire pos = y > 0;
    wire [W:0] xn = {x, 1'b1} + {(pos ? ysh : ~ysh), ~pos};   // x + y or x - y
    wire [W:0] yn = {y, 1'b1} + {(pos ? ~xsh : xsh), pos};    // y - x or y + x
    always @(posedge clk) begin
        done <= 1'b0;
        if (start) begin
            x    <= (x_in < 0) ? -x_in : x_in;
            y    <= (x_in < 0) ? -y_in : y_in;
            ang  <= (x_in < 0) ? 16'd32768 : 16'd0;
            i    <= 5'd0;
            busy <= 1'b1;
        end else if (busy) begin
            x   <= xn[W:1];
            y   <= yn[W:1];
            ang <= ang + (pos ? atan_lut(i) : -atan_lut(i));
            if (i == N - 1) begin busy <= 1'b0; done <= 1'b1; end
            i <= i + 5'd1;
        end
    end
endmodule
`default_nettype wire
