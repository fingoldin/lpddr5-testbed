#!/usr/bin/env bash
# Build the whole design from scratch:  scripts/pcb/build.sh [--no-gates] [--render <outdir>] [--rehearse <workdir>] [--fab]
#
# ONE command, also on a clean tree (every generated file deleted).  Everything comes from the spec in scripts/build_netlist.py
# (+ scripts/data_side.py, scripts/power_side.py); the order below is the only one that works, because the board reads the schematic's
# netlist export and the schematic check reads the project file the board stage writes:
#
#   1. spec        scripts/build_netlist.py  -> design/netlist.json;  scripts/check_netlist.py verifies it
#   2. schematic   scripts/gen_schematic.py  -> *.kicad_sch, lib/lpddr5_testbed.kicad_sym, sym-lib-table, design/erc_settings.json
#   3. board       the stages below on lpddr5-testbed.kicad_pcb (s00 also writes lpddr5-testbed.kicad_pro, .kicad_dru, fp-lib-table)
#   4. gates       check_netlist.py (spec), check_schematic.py (ERC, schematic = spec pin by pin; refreshes doc/lpddr5-testbed.pdf when its
#                  text changed), check_pcb.py (board, gates 1-9; gate 7 writes design/gateware_deskew.json)
#   5. gateware    scripts/gen_gateware.py   -> design/gateware/ (pin constraints, pin map, bring-up notes) from the netlist and the deskew table
#   6. --fab       scripts/make_fab.py       -> fab/ (Gerber X2, drill, IPC-D-356, placement, BOM, drawings, STEP), read back and compared with the
#                  board by scripts/check_fab.py; fab/README.md records the SHA-256 of the board file.  Not part of the default build: the
#                  exports carry a creation time, so they are not byte-reproducible.  Also refreshes the whole-board images in doc/img/
#
# Each board stage tags what it creates (UUID prefix, see common.py) and removes its own earlier output when it is run again, so a single
# stage can also be re-run by hand:
#     python3 scripts/pcb/s10_escape.py && python3 scripts/pcb/s90_zones.py && python3 scripts/pcb/check_pcb.py
#
# Stages:
#   s00_board   empty board, stackup, rules, net classes, .kicad_dru, footprints (parts, test pads, fiducials), nets, fabrication notes
#   s10_escape  U1 fan-out: via-in-pads, links, straps (the unused byte DQ8-15 is open: nothing is drawn for it); field vias; finger vias
#   s20_cmd     command bus: finger stubs of bank 65, divider links, In3 bundle with its tuning features, CK pair; measured by gate 6
#   s30_data    the data side as one stage ('T at the divider', comb): write group (finger stubs of bank 64, 240 ohm / 150 ohm back to back at
#               the node via, F.Cu comb from the balls), read group (In3 branches from the node vias, ordered bundle, ramp, read vias, finger
#               tracks), WCK pair on B.Cu, RDQS pair on In3 from U1 to a via pair just above fingers 219 / 221 and a 5.8 mm F.Cu drop into
#               them, its skew bump, reference networks with the VREF_RD bus and the DAC network; measured by gate 7 (delays with the bend
#               model, T compactness, spacings, crossings)
#   s50_power   power and slow nets (scripts/power_side.py): the 1.2 V strip on F.Cu with its arm, U2 on F.Cu, U3 / U4 with their parts on
#               B.Cu, the 3.3 V trunk, VDD1 / VDDQ lanes and rails, the VDDQ / VDD2H pours under U1, the DAC with SCL / SDA, RESET_n (one via,
#               B.Cu to finger 232); every GND pad on its own via - NO ground pour on F.Cu / B.Cu and no stitching via (as upstream);
#               measured by gate 9 (zero unconnected items, DC budget incl. the In2 island, no layer hop, power pours, command stub ends, GND
#               pad audit, no open via at a solder land, outer-layer copper coverage)
#   s90_zones   inner planes, islands, finger-area keep-outs, zone fill                       (always last)
#
# --rehearse <workdir>: rehearse.py draws every planned centre line of the floorplan that is not routed yet (finger stubs, In3 command
# traces and CK pair, every wire of the data side) on a copy of the board in <workdir>, runs the DRC on the copy and checks with deliberate
# violations that the spacing and pair rules of the .kicad_dru fire.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
gates=1
render=""
rehearse=""
fab=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-gates) gates=0 ;;
    --render) shift; render="$1" ;;
    --rehearse) shift; rehearse="$1" ;;
    --fab) fab=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
run() {   # pcbnew prints harmless wxWidgets assert lines on import: drop them, keep the exit status
  python3 "$@" 2> >(grep -v -e PROPERTY_ENUM -e "duplicate image handler" -e "^$" >&2)
}

echo "== spec =="
run "$root/scripts/build_netlist.py" | sed -n 1p
run "$root/scripts/check_netlist.py" | tail -1

echo "== schematic =="
run "$root/scripts/gen_schematic.py" | tail -1

echo "== board stages =="
for stage in s00_board s10_escape s20_cmd s30_data s50_power s90_zones; do
  run "$here/$stage.py"
done

if [ -n "$render" ]; then
  echo "== renders =="
  run "$here/render.py" "$render" | tail -3
fi

if [ "$gates" = 1 ]; then
  echo "== gates =="
  run "$root/scripts/check_netlist.py" | tail -1
  run "$root/scripts/check_schematic.py" --pdf "$root/doc/lpddr5-testbed.pdf" | tail -4
  run "$here/check_pcb.py"
  echo "== gateware hand-off =="
  run "$root/scripts/gen_gateware.py" | tail -2
  run "$root/scripts/gen_gateware.py" --check
fi

if [ -n "$rehearse" ]; then
  echo "== rehearsal of the floorplan (on a copy) =="
  run "$here/rehearse.py" "$rehearse" --negative | grep "^\[rehearse\]"
fi

if [ "$fab" = 1 ]; then
  echo "== fabrication package (fab/) and layer images (doc/img/) =="
  run "$root/scripts/make_fab.py" | grep -v "^\[PASS\]"
  run "$here/render.py" "$root/doc/img" --only layer_,asm_,all_copper | sed -n '$p'
fi
