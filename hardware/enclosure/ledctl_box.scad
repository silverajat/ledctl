// ledctl v1 enclosure - base + lid, for the 100 x 62 mm controller board.
// Board coordinates (bx, by) are in mm from the board's top-left corner, as in KiCad.
// The box hangs on the headboard with the USB-C edge pointing DOWN and the back (floor) against the fabric.
// part = "base" | "lid" | "assembly"
part = "assembly";

$fn = 48;
BW = 100; BH = 62;                 // board
wall = 2.0; clr = 0.6;             // wall thickness, board-to-wall clearance
floor_t = 3.0;                     // thicker floor holds the bobby-pin channels
standoff = 4.0; pcb = 1.6;
top_clear = 11.5;                  // above the board: TT carrier stack is ~7.5 mm, strip socket 8.5 mm
lid_t = 2.0; lip_h = 1.6; lip_w = 1.2; lip_gap = 0.25;
r_out = 3;

IW = BW + 2 * clr; ID = BH + 2 * clr;
OW = IW + 2 * wall; OD = ID + 2 * wall;
z_pb = floor_t + standoff;         // board bottom
z_pt = z_pb + pcb;                 // board top
base_h = z_pt + top_clear;

// board coords -> box coords (box Y grows from the USB edge upwards)
function X(bx) = wall + clr + bx;
function Y(by) = wall + clr + (BH - by);

module rbox(w, d, h, r) {
    hull() for (x = [r, w - r]) for (y = [r, d - r]) translate([x, y, 0]) cylinder(r = r, h = h);
}
// rectangle given in board coords (x0,y0)-(x1,y1), extruded in z
module brect(x0, y0, x1, y1, z0, z1, m = 0) {
    translate([X(x0) - m, Y(y1) - m, z0]) cube([x1 - x0 + 2 * m, y1 - y0 + 2 * m, z1 - z0]);
}

holes = [[44.6, 3.2], [96.8, 3.2], [3.0, 44.6], [96.8, 58.8]];   // M2.5 board holes H1..H4

module base() {
    difference() {
        rbox(OW, OD, base_h, r_out);
        translate([wall, wall, floor_t]) rbox(IW, ID, base_h, r_out - wall);
        // USB-C (J1, centre bx=8) through the bottom wall
        translate([X(8) - 5.4, -1, z_pt - 0.7]) cube([10.8, wall + 2, 4.6]);
        // strip cable exit: notch in the left wall above the strip socket J4
        translate([-1, Y(36.8), z_pt + 2.5]) cube([wall + 2, 36.8 - 24.8, base_h]);
        // microphone vents under the board (bottom-port mics MK1/MK2 hear through the PCB)
        for (bx = [55 : 2.6 : 72]) translate([X(bx) - 0.65, -1, floor_t + 0.8]) cube([1.3, wall + 2, standoff - 1.4]);
        // bobby-pin channels in the back face (pin slides up under a bridge)
        for (p = [[8, 6], [OW - 8, 6], [8, OD - 22], [OW - 8, OD - 22]])
            translate([p[0] - 1.1, p[1], -0.01]) {
                cube([2.2, 16, 1.9]);                       // groove (1.9 deep)
            }
        // M2.5 screw pilots through the floor (optional screws from the back)
        for (h = holes) translate([X(h[0]), Y(h[1]), -1]) cylinder(d = 2.2, h = floor_t + standoff + 1);
        // engraved label on the back
        translate([OW / 2, OD / 2, -0.01]) mirror([1, 0, 0]) linear_extrude(0.6)
            text("ledctl v1", size = 6, halign = "center", valign = "center");
    }
    // standoffs (M2.5 self-tapping, 2.2 mm pilot)
    for (h = holes) translate([X(h[0]), Y(h[1]), floor_t - 0.01]) difference() {
        cylinder(d = 5.4, h = standoff);
        translate([0, 0, 0.5]) cylinder(d = 2.2, h = standoff);
    }
    // bridges over the bobby-pin grooves (top end of each groove)
    for (p = [[8, 6], [OW - 8, 6], [8, OD - 22], [OW - 8, OD - 22]])
        translate([p[0] - 1.1, p[1] + 11, 0]) cube([2.2, 3.5, 0.7]);   // 0.7 mm skin -> 1.2 mm tunnel for the pin wire
}

module lid() {
    difference() {
        union() {
            rbox(OW, OD, lid_t, r_out);
            // friction lip that drops inside the walls
            translate([wall + lip_gap, wall + lip_gap, -lip_h]) difference() {
                rbox(IW - 2 * lip_gap, ID - 2 * lip_gap, lip_h + 0.2, r_out - wall);
                translate([lip_w, lip_w, -1]) rbox(IW - 2 * lip_gap - 2 * lip_w, ID - 2 * lip_gap - 2 * lip_w, lip_h + 1, 1);
            }
        }
        zz = -lip_h - 1; hh = lid_t + lip_h + 2;
        brect(0.0, 24.6, 5.0, 37.0, zz, hh, 0.3);         // strip plug (J4) drops in from above
        brect(25.19, 57.59, 41.51, 61.22, zz, hh, 0.4);    // JTAG header J2
        brect(43.0, 56.6, 49.0, 61.6, zz, hh, 0.3);        // IR receiver window (U7)
        for (s = [[54.0, 59.0], [23.5, 9.5], [5.0, 21.0]]) // RECONFIG, ESP RESET, ESP BOOT: poke with a pin
            translate([X(s[0]), Y(s[1]), zz]) cylinder(d = 3.0, h = hh);
        for (d = [[35.41, 56.55], [24.05, 25.6], [24.48, 27.33], [13.0, 19.5]])   // LED windows D1-D4
            translate([X(d[0]), Y(d[1]), zz]) cylinder(d = 2.2, h = hh);
        // engraved top text
        translate([X(73), Y(30), lid_t - 0.6]) linear_extrude(1)
            text("ledctl", size = 9, halign = "center", valign = "center");
        translate([X(73), Y(41), lid_t - 0.5]) linear_extrude(1)
            text("music-sync LED controller", size = 2.8, halign = "center", valign = "center");
        // button labels
        translate([X(54), Y(54.2), lid_t - 0.5]) linear_extrude(1) text("RECONFIG", size = 2.2, halign = "center");
        translate([X(23.5), Y(5.0), lid_t - 0.5]) linear_extrude(1) text("RESET", size = 2.2, halign = "center");
        translate([X(5.0) + 3.5, Y(21) - 0.8, lid_t - 0.5]) linear_extrude(1) text("BOOT", size = 2.2);
        translate([X(33.4), Y(55.6), lid_t - 0.5]) linear_extrude(1) text("JTAG", size = 2.2, halign = "center");
    }
}

module board_ghost() {
    color("green", 0.6) translate([X(0), Y(BH), z_pb]) cube([BW, BH, pcb]);
    color("gray", 0.5) translate([X(48), Y(56), z_pt + 5]) cube([50, 50, 1.6]);   // TT carrier (mezzanine)
    color("black") translate([X(0.75), Y(36.44), z_pt]) cube([3.6, 11.3, 8.5]);   // strip socket
}

if (part == "base") base();
else if (part == "lid") translate([0, OD, lid_t]) rotate([180, 0, 0]) lid();   // print lid face-down
else { base(); board_ghost(); color("white", 0.35) translate([0, 0, base_h + 8]) lid(); }

echo(str("outer size: ", OW, " x ", OD, " x ", base_h + lid_t, " mm"));
