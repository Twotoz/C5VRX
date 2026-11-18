// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL combinational
// multipliers on the GW1N DSP blocks (MULT9X9 / MULT18X18, no registers).
// Synthesis instantiates the primitive (Yosys defines SYNTHESIS); simulation
// uses the behavioural product. The build runs synth_gowin -nodsp, so only
// these instances use DSP blocks and the small products stay in logic.
`default_nettype none
module mul9 #(parameter A_SIGNED = 1, B_SIGNED = 1) (
    input  wire [8:0]  a,
    input  wire [8:0]  b,
    output wire [17:0] y
);
`ifdef SYNTHESIS
    MULT9X9 m (.CLK(1'b0), .CE(1'b0), .RESET(1'b0), .A(a), .SIA(9'd0), .ASEL(1'b0),
               .ASIGN(A_SIGNED ? 1'b1 : 1'b0), .B(b), .SIB(9'd0), .BSEL(1'b0),
               .BSIGN(B_SIGNED ? 1'b1 : 1'b0), .DOUT(y), .SOA(), .SOB());
`else
    wire signed [9:0] as = A_SIGNED ? {a[8], a} : {1'b0, a};
    wire signed [9:0] bs = B_SIGNED ? {b[8], b} : {1'b0, b};
    wire signed [19:0] p = as * bs;
    assign y = p[17:0];
`endif
endmodule

module mul18 #(parameter A_SIGNED = 1, B_SIGNED = 1) (
    input  wire [17:0] a,
    input  wire [17:0] b,
    output wire [35:0] y
);
`ifdef SYNTHESIS
    MULT18X18 m (.CLK(1'b0), .CE(1'b0), .RESET(1'b0), .A(a), .SIA(18'd0), .ASEL(1'b0),
                 .ASIGN(A_SIGNED ? 1'b1 : 1'b0), .B(b), .SIB(18'd0), .BSEL(1'b0),
                 .BSIGN(B_SIGNED ? 1'b1 : 1'b0), .DOUT(y), .SOA(), .SOB());
`else
    wire signed [18:0] as = A_SIGNED ? {a[17], a} : {1'b0, a};
    wire signed [18:0] bs = B_SIGNED ? {b[17], b} : {1'b0, b};
    wire signed [37:0] p = as * bs;
    assign y = p[35:0];
`endif
endmodule
`default_nettype wire
