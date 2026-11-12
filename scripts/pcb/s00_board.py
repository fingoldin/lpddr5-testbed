#!/usr/bin/env python3
"""Stage s00: the board without copper.

Writes, from design/netlist.json only:
  lpddr5-testbed.kicad_pro   board rules, net classes and their net assignments
  lpddr5-testbed.kicad_dru   the rules a net class cannot express
  fp-lib-table               project footprint library
  lpddr5-testbed.kicad_pcb   empty 6-layer board (stackup of the spec), J1 (which carries the outline) and
                             every part at its spec position, every pad on its net

The board file is started from a text header, not from an existing board.  Nothing is routed here.
The schematic files and the sym-lib-table are not touched.
"""
import json
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

STAGE = "s00_board"

# Parts whose reference would not fit on the silkscreen clear of pads: it is shown on the Fab layer only.
SILK_TEXT_H = 0.50
SILK_TEXT_T = 0.08
FAB_TEXT_H = 0.25
FAB_TEXT_T = 0.04
BOARD_ID = "LPDDR5-CMD-TESTBED  ZCU104  R%s"


# ------------------------------------------------------------------------------------------------ project
def netclass_table(spec):
    """KiCad net classes from rules.netclasses.  One width per class (the width of the layer that carries
    the long run); the per-layer widths are enforced by the .kicad_dru."""
    nc = spec["rules"]["netclasses"]
    vias = spec["rules"]["vias"]
    out = []

    def primary(layers):
        for key in ("In3.Cu", "B.Cu", "F.Cu", "any"):
            if key in layers:
                return layers[key]
        return next(iter(layers.values()))
    for prio, (name, c) in enumerate(nc.items()):
        if c.get("same_nets_as"):
            continue        # rules of another use of the nets of an existing class (RD_DQ: the read branch of the DQ nets): no KiCad class of its own
        lay = primary(c["layers"])
        pair = next((l for l in c["layers"].values() if "gap" in l), None)
        power = name == "POWER"
        e = {
            "bus_width": 12,
            "clearance": c.get("clearance_other_layers", c["clearance"]),
            "diff_pair_gap": pair["gap"] if pair else 0.1,
            "diff_pair_via_gap": 0.25,
            "diff_pair_width": pair["width"] if pair else 0.1,
            "line_style": 0,
            "microvia_diameter": 0.3,
            "microvia_drill": 0.1,
            "name": name,
            "pcb_color": "rgba(0, 0, 0, 0.000)",
            "priority": 2147483647 if name == "Default" else prio,
            "schematic_color": "rgba(0, 0, 0, 0.000)",
            "track_width": lay["width"],
            "tuning_profile": "",
            "via_diameter": vias["through_power"]["diameter"] if power else vias["through_signal"]["diameter"],
            "via_drill": vias["through_power"]["drill"] if power else vias["through_signal"]["drill"],
            "wire_width": 6,
        }
        out.append(e)
    out.sort(key=lambda e: 0 if e["name"] == "Default" else 1)
    patterns = []
    for name, c in nc.items():
        if name == "Default":
            continue
        for n in c["nets"]:
            patterns.append({"netclass": name, "pattern": n})
            patterns.append({"netclass": name, "pattern": "/" + n})      # same net if the schematic names it with a sheet path
    return out, patterns


RULE_SEVERITIES = {
    "annular_width": "error", "clearance": "error", "connection_width": "warning", "copper_edge_clearance": "error",
    "copper_sliver": "warning", "courtyards_overlap": "error", "creepage": "error",
    # the gap range only tells the DRC which segments count as coupled (for the uncoupled-length limit, an error);
    # it also flags the places where the legs must part (node vias, the split at H9), hence a warning.  The minimum
    # gap is enforced as a clearance rule (error).
    "diff_pair_gap_out_of_range": "warning",
    "diff_pair_uncoupled_length_too_long": "error", "drill_out_of_range": "error", "duplicate_footprints": "warning",
    "extra_footprint": "warning", "footprint": "error", "footprint_filters_mismatch": "ignore",
    "footprint_symbol_field_mismatch": "warning", "footprint_symbol_mismatch": "warning", "footprint_type_mismatch": "error",
    "hole_clearance": "error", "hole_near_hole": "error", "hole_to_hole": "error", "holes_co_located": "warning",
    "invalid_outline": "error", "isolated_copper": "warning", "item_on_disabled_layer": "error", "items_not_allowed": "error",
    "length_out_of_range": "error", "lib_footprint_issues": "warning", "lib_footprint_mismatch": "warning",
    "malformed_courtyard": "error", "microvia_drill_out_of_range": "error", "mirrored_text_on_front_layer": "warning",
    "missing_courtyard": "ignore", "missing_footprint": "warning", "missing_tuning_profile": "warning", "net_conflict": "warning",
    "nonmirrored_text_on_back_layer": "warning", "npth_inside_courtyard": "ignore", "padstack": "error",
    "pth_inside_courtyard": "ignore", "shorting_items": "error", "silk_edge_clearance": "warning", "silk_over_copper": "warning",
    "silk_overlap": "warning", "skew_out_of_range": "error", "solder_mask_bridge": "error", "starved_thermal": "error",
    "text_height": "warning", "text_on_edge_cuts": "error", "text_thickness": "warning",
    "through_hole_pad_without_hole": "error", "too_many_vias": "error", "track_angle": "error", "track_dangling": "warning",
    "track_not_centered_on_via": "ignore", "track_on_post_machined_layer": "error", "track_segment_length": "error",
    "track_width": "error", "tracks_crossing": "error", "tuning_profile_track_geometries": "ignore",
    "unconnected_items": "error", "unresolved_variable": "error", "via_dangling": "warning", "zone_has_empty_net": "error",
    "zones_intersect": "error",
}


def project_json(spec, keep):
    r = spec["rules"]["board"]
    v = spec["rules"]["vias"]
    classes, patterns = netclass_table(spec)
    tune = {"corner_radius_percentage": 80, "corner_style": 1, "max_amplitude": 1.0, "min_amplitude": 0.2,
            "single_sided": False, "spacing": 0.6}
    cmd_tune = spec["rules"]["netclasses"]["CMD_SE"]["tuning"]
    td = {"td_allow_use_two_tracks": True, "td_curve_segcount": 0, "td_height_ratio": 1.0, "td_length_ratio": 0.5,
          "td_maxheight": 2.0, "td_maxlen": 1.0, "td_on_pad_in_zone": False, "td_width_to_size_filter_ratio": 0.9}
    pro = {
        "board": {
            "3dviewports": [],
            "design_settings": {
                "defaults": {
                    "apply_defaults_to_fp_barcodes": False, "apply_defaults_to_fp_dimensions": False,
                    "apply_defaults_to_fp_fields": False, "apply_defaults_to_fp_shapes": False,
                    "apply_defaults_to_fp_text": False,
                    "board_outline_line_width": 0.05, "copper_line_width": 0.2, "copper_text_italic": False,
                    "copper_text_size_h": 1.0, "copper_text_size_v": 1.0, "copper_text_thickness": 0.15,
                    "copper_text_upright": False, "courtyard_line_width": 0.05, "dimension_precision": 4,
                    "dimension_units": 3,
                    "dimensions": {"arrow_length": 1270000, "extension_offset": 500000, "keep_text_aligned": True,
                                   "suppress_zeroes": False, "text_position": 0, "units_format": 1},
                    "fab_line_width": 0.1, "fab_text_italic": False, "fab_text_size_h": 0.5, "fab_text_size_v": 0.5,
                    "fab_text_thickness": 0.08, "fab_text_upright": False, "other_line_width": 0.1,
                    "other_text_italic": False, "other_text_size_h": 0.5, "other_text_size_v": 0.5,
                    "other_text_thickness": 0.08, "other_text_upright": False,
                    "pads": {"drill": 0.0, "height": 0.4, "width": 0.4},
                    "silk_line_width": 0.12, "silk_text_italic": False, "silk_text_size_h": SILK_TEXT_H,
                    "silk_text_size_v": SILK_TEXT_H, "silk_text_thickness": SILK_TEXT_T, "silk_text_upright": False,
                    "zones": {"45_degree_only": False, "border_display_style": 2, "border_hatch_pitch": 0.5,
                              "corner_radius": 0.0, "corner_smoothing": 0, "fill_mode": 0, "hatch_gap": 1.5,
                              "hatch_orientation": 0.0, "hatch_smoothing_level": 0, "hatch_smoothing_value": 0.1,
                              "hatch_thickness": 1.0, "min_clearance": 0.1, "min_island_area": 10.0,
                              "min_thickness": 0.1, "pad_connection": 2, "remove_islands": 0,
                              "thermal_relief_gap": 0.25, "thermal_relief_spoke_width": 0.25},
                },
                "diff_pair_dimensions": [{"gap": 0.0, "via_gap": 0.0, "width": 0.0},
                                         {"gap": 0.1, "via_gap": 0.25, "width": 0.1}],
                "drc_exclusions": [],
                "meta": {"version": 2},
                "rule_severities": RULE_SEVERITIES,
                "rules": {
                    "allow_blind_buried_vias": r["blind_buried_vias"],
                    "allow_microvias": r["microvias"],
                    "max_error": 0.005,
                    "min_clearance": r["min_clearance"],
                    "min_connection": 0.0,
                    "min_copper_edge_clearance": r["copper_to_edge"],
                    "min_groove_width": 0.0,
                    "min_hole_clearance": r["hole_clearance"],
                    "min_hole_to_hole": r["hole_to_hole"],
                    "min_microvia_diameter": 0.2,
                    "min_microvia_drill": 0.1,
                    "min_resolved_spokes": 2,
                    "min_silk_clearance": 0.0,
                    "min_text_height": 0.4,
                    "min_text_thickness": 0.06,
                    "min_through_hole_diameter": r["min_through_hole"],
                    "min_track_width": r["min_track"],
                    "min_via_annular_width": r["min_annular_ring"],
                    "min_via_diameter": r["min_via_diameter"],
                    "solder_mask_to_copper_clearance": 0.0,
                    "use_height_for_length_calcs": True,
                },
                "teardrop_options": [{"td_onpthpad": True, "td_onroundshapesonly": False, "td_onsmdpad": True,
                                      "td_ontrackend": False, "td_onvia": True}],
                "teardrop_parameters": [dict(td, td_target_name=n) for n in ("td_round_shape", "td_rect_shape", "td_track_end")],
                "track_widths": [0.0, 0.09, 0.1, 0.15, 0.2, 0.3],
                "tuning_pattern_settings": {
                    "diff_pair_defaults": dict(tune, spacing=1.0),
                    "diff_pair_skew_defaults": dict(tune),
                    "single_track_defaults": dict(tune, max_amplitude=cmd_tune["amplitude_max"], min_amplitude=0.1,
                                                  spacing=cmd_tune["leg_pitch_min"], single_sided=True),
                },
                "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                   {"diameter": v["via_in_pad"]["diameter"], "drill": v["via_in_pad"]["drill"]},
                                   {"diameter": v["through_signal"]["diameter"], "drill": v["through_signal"]["drill"]},
                                   {"diameter": v["through_power"]["diameter"], "drill": v["through_power"]["drill"]}],
                "zones_allow_external_fillets": False,
                "zones_use_no_outline": True,
            },
            "ipc2581": {"bom_rev": "", "dist": "", "distpn": "", "internal_id": "", "mfg": "", "mpn": "",
                        "sch_revision": spec["meta"]["revision"]},
            "layer_pairs": [],
            "layer_presets": [],
            "viewports": [],
        },
        "boards": [],
        "component_class_settings": {"assignments": [], "meta": {"version": 0}, "sheet_component_classes": {"enabled": False}},
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": C.PROJECT + ".kicad_pro", "version": 3},
        "net_settings": {"classes": classes, "meta": {"version": 5}, "net_colors": None,
                         "netclass_assignments": None, "netclass_patterns": patterns},
        "pcbnew": {"last_paths": {"gencad": "", "idf": "", "netlist": "", "plot": "", "pos_files": "",
                                  "specctra_dsn": "", "step": "", "svg": "", "vrml": ""},
                   "page_layout_descr_file": ""},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": [], "meta": {"version": 1}},
        "sheets": [],
        "text_variables": {},
        "tuning_profiles": {"meta": {"version": 0}, "tuning_profiles_impedance_geometric": []},
    }
    # sections owned by the schematic are kept if this script wrote the file before (see write_project)
    for k, val in keep.items():
        pro[k] = val
    return pro


def write_project(spec):
    """The schematic-owned sections (schematic, erc, sheets, libraries, cvpcb, text_variables) are kept when the
    existing project file is one of ours (it has the CMD_SE net class); an older, upstream-derived project file is
    replaced completely."""
    keep = {}
    if os.path.exists(C.PRO):
        try:
            old = json.load(open(C.PRO))
            ours = any(c.get("name") == "CMD_SE" for c in old.get("net_settings", {}).get("classes", []))
            if ours:
                for k in ("schematic", "erc", "sheets", "libraries", "cvpcb", "text_variables"):
                    if k in old:
                        keep[k] = old[k]
        except (ValueError, OSError):
            pass
    # the "erc" section is owned by the schematic generator (design/erc_settings.json: rule severities and the two exclusions
    # of the strapped DMI balls): the project file always carries it, so that ERC in KiCad gives what scripts/check_schematic.py gives
    erc_file = os.path.join(C.ROOT, "design", "erc_settings.json")
    if os.path.exists(erc_file):
        try:
            keep["erc"] = json.load(open(erc_file))["erc"]
        except (ValueError, OSError, KeyError):
            pass
    pro = project_json(spec, keep)
    with open(C.PRO, "w") as fh:
        json.dump(pro, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return len(pro["net_settings"]["classes"]), len(pro["net_settings"]["netclass_patterns"]), bool(keep)


def write_dru(spec):
    nc = spec["rules"]["netclasses"]
    rb = spec["rules"]["board"]
    L = []
    A = L.append
    A("(version 1)")
    A("# lpddr5-testbed.kicad_dru: written by scripts/pcb/s00_board.py from design/netlist.json. Do not edit by hand.")
    A("# Later rules win over earlier ones and over the net-class values of the project file.")
    A("")
    A("# ---- generic width rules first: a later rule wins, so the class rules below override these ----")
    A("# ---- the %g mm neck-down exists only inside the courtyard of U1 ----" % rb["min_track"])
    A('(rule "neck_only_under_U1"')
    A('  (condition "A.Type == \'Track\' && !A.intersectsCourtyard(\'U1\')")')
    A("  (constraint track_width (min 0.1mm)))")
    A('(rule "POWER_width"')
    A('  (condition "A.NetClass == \'POWER\' && !A.intersectsCourtyard(\'U1\')")')
    A("  (constraint track_width (min %gmm)))" % nc["POWER"]["layers"]["any"]["width"])
    A("")
    A("# ---- per-class, per-layer track widths (rules.netclasses[*].layers) ----")
    for name, c in nc.items():
        if name in ("Default", "POWER"):
            continue
        kname = c.get("same_nets_as", name)       # RD_DQ: the nets are those of WR_DQ; its rules cover the layers that class does not use
        for layer, g in c["layers"].items():
            if layer == "any" or (kname != name and layer in nc[kname]["layers"]):
                continue
            A('(rule "%s_%s_width"' % (name, layer.replace(".", "_")))
            A('  (layer "%s")' % layer)
            A('  (condition "A.NetClass == \'%s\'")' % kname)
            # max_width: a wider section the spec lists (the board gate checks every other width exactly)
            # min_width: a narrower section the spec lists (RD_DQ: the neck of the finger track)
            A("  (constraint track_width (min %gmm) (opt %gmm) (max %gmm)))" % (g.get("min_width", g["width"]), g["width"], g.get("max_width", g["width"])))
            if "neck_width" in g:
                A('(rule "%s_%s_neck_under_U1"' % (name, layer.replace(".", "_")))
                A('  (layer "%s")' % layer)
                A('  (condition "A.NetClass == \'%s\' && A.intersectsCourtyard(\'U1\')")' % name)
                A("  (constraint track_width (min %gmm) (opt %gmm) (max %gmm)))" % (g["neck_width"], g["width"], g.get("max_width", g["width"])))
    A("")
    A("# ---- clearances.  The last matching rule wins: general rules first, the intra-pair gap last ----")
    for name, c in nc.items():
        if "clearance_layer" in c:
            A('(rule "%s_%s_clearance"' % (name, c["clearance_layer"].replace(".", "_")))
            A('  (layer "%s")' % c["clearance_layer"])
            A('  (condition "A.NetClass == \'%s\'")' % name)
            A("  (constraint clearance (min %gmm)))" % c["clearance"])
    for r_ in spec["rules"]["spacing_rules"]:
        if r_.get("condition"):                # a rule given with its whole condition (area rules, pad rules)
            A('(rule "%s"' % r_["name"])
            if r_.get("layer"):
                A('  (layer "%s")' % r_["layer"])
            A('  (condition "%s")' % r_["condition"])
            A("  (constraint clearance (min %gmm)))" % r_["min"])
            continue
        cond = ["A.NetClass == '%s'" % r_["a_class"]]
        if r_.get("a_type"):
            cond.append("A.Type == '%s'" % r_["a_type"])
        if r_.get("b_type") and r_["b_type"] != "any":
            cond.append("B.Type == '%s'" % r_["b_type"])
        elif r_.get("b_type") == "any":
            cond.append("(B.Type == 'Track' || B.Type == 'Via')")
        if r_.get("b_classes"):
            cond.append("(" + " || ".join("B.NetClass == '%s'" % k for k in r_["b_classes"]) + ")")
        for k in r_.get("b_not_classes", []):
            cond.append("B.NetClass != '%s'" % k)
        cond.append("A.Net != B.Net")
        if r_.get("where") == "outside_u1":
            cond += ["!A.intersectsCourtyard('U1')", "!B.intersectsCourtyard('U1')"]
        if r_.get("a_not_in_area"):          # a named rule area of floorplan.rule_areas (drawn by s90_zones)
            cond.append("!A.intersectsArea('%s')" % r_["a_not_in_area"])
        if r_.get("a_not_enclosed_by_area"):  # only an item that lies wholly inside the area is left out (a long track that merely passes through it is not)
            ar_ = r_["a_not_enclosed_by_area"]
            for an_ in ([ar_] if isinstance(ar_, str) else ar_):
                cond.append("!A.enclosedByArea('%s')" % an_)
        A('(rule "%s"' % r_["name"])
        if r_.get("layer"):
            A('  (layer "%s")' % r_["layer"])
        A('  (condition "%s")' % " && ".join(cond))
        A("  (constraint clearance (min %gmm)))" % r_["min"])
    A("")
    A("# ---- differential pairs.  KiCad pairs nets by a trailing _P / _N: CK_P / CK_N, WCK_P / WCK_N, RDQS_P / RDQS_N.")
    A("#      The finger-side pairs (CK_F, WCK_F) fan from 0.5 mm finger pitch to their divider columns and are")
    A("#      deliberately not under the gap rule. ----")
    for name, base in (("CMD_CK", "CK"), ("WR_WCK", "WCK"), ("RD_RDQS", "RDQS")):
        g = next(l for l in nc[name]["layers"].values() if "gap" in l)
        A('(rule "%s_intra_pair_gap"' % name)
        A('  (condition "A.NetClass == \'%s\' && B.NetClass == \'%s\' && A.Net != B.Net")' % (name, name))
        A("  (constraint clearance (min %gmm)))" % g["gap"])
        A('(rule "%s_pair_coupling"' % name)
        A('  (condition "A.inDiffPair(\'%s\')")' % base)
        A("  (constraint diff_pair_gap (min %gmm) (opt %gmm) (max %gmm))" % (g["gap"] - 0.01, g["gap"], g["gap"] + 0.01))
        A("  (constraint diff_pair_uncoupled (max %gmm)))" % nc[name]["max_uncoupled_mm"])
        A('(rule "%s_pair_skew"' % name)
        A('  (condition "A.inDiffPair(\'%s\')")' % base)
        A("  (constraint skew (max %gmm) (within_diff_pairs)))" % nc[name]["max_skew_mm"])
    A("")
    A("# ---- stubs on the 1.2 V side of the dividers and on the reference fingers ----")
    A('(rule "HOST_SE_stub_length"')
    A('  (condition "A.NetClass == \'HOST_SE\'")')
    A("  (constraint length (max %gmm)))" % nc["HOST_SE"]["max_length"])
    A('(rule "VREF_N_stub_length"')
    A('  (condition "A.NetName == \'VREF_N?\' || A.NetName == \'/VREF_N?\'")')
    A("  (constraint length (max %gmm)))" % nc["VREF"]["max_stub_length"])
    A("")
    A("# ---- card edge: finger pads are 0.35 mm on a 0.50 mm pitch and start 0.15 mm from the edge ----")
    A('(rule "finger_pad_to_finger_pad"')
    A('  (condition "A.Type == \'Pad\' && B.Type == \'Pad\' && A.memberOfFootprint(\'J1\') && B.memberOfFootprint(\'J1\')")')
    A("  (constraint clearance (min 0.14mm)))")
    A('(rule "track_to_foreign_finger_pad"')
    A('  (condition "A.Type == \'Pad\' && A.memberOfFootprint(\'J1\') && B.Type != \'Pad\'")')
    A("  (constraint clearance (min 0.1mm)))")
    A('(rule "edge_clearance_everything_but_the_fingers"')
    A('  (condition "!A.memberOfFootprint(\'J1\')")')
    A("  (constraint edge_clearance (min 0.3mm)))")
    A("")
    with open(C.DRU, "w") as fh:
        fh.write("\n".join(L))
    return sum(1 for l in L if l.startswith("(rule"))


def write_fp_lib_table(spec):
    """Project library plus the stock libraries the design uses, so that the board checks against its libraries
    without relying on a global footprint table."""
    path = os.path.join(C.ROOT, "fp-lib-table")
    libs = sorted({p["footprint"].split(":")[0] for p in spec["parts"]} - {C.PROJECT_LIB})
    with open(path, "w") as fh:
        fh.write('(fp_lib_table\n  (version 7)\n')
        fh.write('  (lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/lib/%s.pretty")(options "")(descr "project footprints: card edge, BGA-315, MLF-6"))\n'
                 % (C.PROJECT_LIB, C.PROJECT_LIB))
        for lib in libs:
            fh.write('  (lib (name "%s")(type "KiCad")(uri "${KICAD10_FOOTPRINT_DIR}/%s.pretty")(options "")(descr "stock KiCad library"))\n' % (lib, lib))
        fh.write(')\n')
    return path


# ------------------------------------------------------------------------------------------------ board header
def board_header(spec):
    st = spec["stackup"]
    lines = []
    A = lines.append
    A("(kicad_pcb")
    A("\t(version 20260206)")
    A('\t(generator "pcbnew")')
    A('\t(generator_version "10.0")')
    A("\t(general")
    A("\t\t(thickness %g)" % st["total_thickness_with_mask_mm"])
    A("\t\t(legacy_teardrops no)")
    A("\t)")
    A('\t(paper "A4")')
    A("\t(title_block")
    A('\t\t(title "LPDDR5 command-bus testbed, DDR4 SO-DIMM card for the ZCU104")')
    A('\t\t(date "%s")' % spec["meta"]["date"])
    A('\t\t(rev "%s")' % spec["meta"]["revision"])
    A('\t\t(comment 1 "Generated by scripts/pcb/build.sh from design/netlist.json")')
    A('\t\t(comment 2 "Finished thickness over the fingers 1.20 +/-0.10 mm; via-in-pad under U1 filled and capped")')
    A('\t\t(comment 3 "Fingers: hard gold over nickel (30 uin min), 0.25 mm x 45 deg chamfer; all other copper ENIG")')
    A('\t\t(comment 4 "Controlled impedance: In3 0.15 mm 46 ohm, In3 pair 85 ohm, outer 0.20 mm 46 ohm, F.Cu / B.Cu pair 92 ohm, +/-10 %")')
    A("\t)")
    A("\t(layers")
    # same names and types as the upstream board: In1 / In2 are 'power', In3 / In4 'signal'
    for lid, name, typ, user in ((0, "F.Cu", "signal", None), (4, "In1.Cu", "power", None), (6, "In2.Cu", "power", None),
                                 (8, "In3.Cu", "signal", None), (10, "In4.Cu", "signal", None), (2, "B.Cu", "signal", None),
                                 (9, "F.Adhes", "user", "F.Adhesive"), (11, "B.Adhes", "user", "B.Adhesive"),
                                 (13, "F.Paste", "user", None), (15, "B.Paste", "user", None),
                                 (5, "F.SilkS", "user", "F.Silkscreen"), (7, "B.SilkS", "user", "B.Silkscreen"),
                                 (1, "F.Mask", "user", None), (3, "B.Mask", "user", None),
                                 (17, "Dwgs.User", "user", "User.Drawings"), (19, "Cmts.User", "user", "User.Comments"),
                                 (21, "Eco1.User", "user", "User.Eco1"), (23, "Eco2.User", "user", "User.Eco2"),
                                 (25, "Edge.Cuts", "user", None), (27, "Margin", "user", None),
                                 (31, "F.CrtYd", "user", "F.Courtyard"), (29, "B.CrtYd", "user", "B.Courtyard"),
                                 (35, "F.Fab", "user", None), (33, "B.Fab", "user", None)):
        A('\t\t(%d "%s" %s%s)' % (lid, name, typ, ' "%s"' % user if user else ""))
    A("\t)")
    A("\t(setup")
    A("\t\t(stackup")
    A('\t\t\t(layer "F.SilkS" (type "Top Silk Screen") (color "White"))')
    A('\t\t\t(layer "F.Paste" (type "Top Solder Paste"))')
    for lay in st["layers"]:
        n, t = lay["layer"], lay["thickness_mm"]
        if lay["type"] == "soldermask":
            A('\t\t\t(layer "%s" (type "%s Solder Mask") (color "Black") (thickness %g))' % (n, "Top" if n[0] == "F" else "Bottom", t))
        elif lay["type"] == "copper":
            A('\t\t\t(layer "%s" (type "copper") (thickness %g))' % (n, t))
        else:
            A('\t\t\t(layer "%s" (type "%s") (thickness %g) (material "%s") (epsilon_r %g) (loss_tangent 0.02))'
              % (n, lay["type"], t, lay["material"], lay["er"]))
    A('\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))')
    A('\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen") (color "White"))')
    A('\t\t\t(copper_finish "ENIG")')
    A("\t\t\t(dielectric_constraints yes)")
    A("\t\t\t(edge_connector yes)")
    A("\t\t)")
    A("\t\t(pad_to_mask_clearance 0.05)")
    A("\t\t(allow_soldermask_bridges_in_footprints no)")
    A("\t\t(tenting (front yes) (back yes))")
    A("\t\t(covering (front no) (back no))")
    A("\t\t(plugging (front no) (back no))")
    A("\t\t(capping no)")
    A("\t\t(filling no)")
    # drill / place origin = the card origin (lower-left corner seen from the front, pin-1 end of the finger edge): position and drill files
    # exported with "use drill / place file origin" are then in card coordinates (x from the pin-1 end, y up from the finger edge after the
    # usual sign flip), not in page coordinates
    ox, oy = spec["floorplan"]["board_origin_kicad_mm"]
    A("\t\t(aux_axis_origin %g %g)" % (ox, oy))
    A("\t\t(grid_origin %g %g)" % (ox, oy))
    A("\t)")
    A(")")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ schematic parity
def _sexpr(text):
    import re as _re
    tok = _re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()]+', text)
    stack, cur = [], []
    for t in tok:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\") if t.startswith('"') else t)
    return cur[0]


def schematic_export():
    """What KiCad's own schematic <-> board comparison ('kicad-cli pcb drc --schematic-parity') looks at beyond the nets of the spec:
    the Datasheet / Description fields of every symbol, and the name KiCad gives the net of every unconnected pin
    ('unconnected-(J1F-DQ5(AG19)-Pad3)').  Read from the schematic's netlist export; ({}, {}) if there is no schematic yet."""
    import subprocess
    import tempfile
    sch = os.path.join(C.ROOT, C.PROJECT + ".kicad_sch")
    if not os.path.exists(sch):
        return {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "sch.net")
        r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0 or not os.path.exists(out):
            return {}, {}
        tree = _sexpr(open(out).read())

    def sub(node, key):
        return [c for c in node if isinstance(c, list) and c and c[0] == key]
    fields, nc = {}, {}
    for comp in sub(sub(tree, "components")[0], "comp"):
        ref = sub(comp, "ref")[0][1]
        fields[ref] = {"Datasheet": (sub(comp, "datasheet") or [[None, ""]])[0][1], "Description": (sub(comp, "description") or [[None, ""]])[0][1]}
    for net in sub(sub(tree, "nets")[0], "net"):
        name = sub(net, "name")[0][1]
        if name.startswith("unconnected-("):
            for node in sub(net, "node"):
                nc[(sub(node, "ref")[0][1], sub(node, "pin")[0][1])] = name
    return fields, nc


# ------------------------------------------------------------------------------------------------ footprints
def load_footprint(lib_name):
    lib, name = lib_name.split(":")
    d = C.PROJECT_LIB_DIR if lib == C.PROJECT_LIB else os.path.join(C.KICAD_FP, lib + ".pretty")
    fp = pcbnew.FootprintLoad(d, name)
    if fp is None:
        raise RuntimeError("footprint %s not found" % lib_name)
    return fp


def expected_pads(spec, ref):
    """{pad number: (x, y)} in the card frame, for the pads the spec positions explicitly."""
    fpl = spec["floorplan"]
    if ref in fpl["placements"]:
        pl = fpl["placements"][ref]
        return {"1": tuple(pl["pad1"]), "2": tuple(pl["pad2"])}
    pl = fpl["placements_other"][ref]
    if isinstance(pl["pads"], dict):
        return {k: tuple(v) for k, v in pl["pads"].items()}
    if ref == "U1":
        return {b: C.u1_ball_xy(b) for b in C.edge.ball_names()}
    if ref == "J1":
        return {str(f["finger"]): (f["x_mm"], C.finger_xy(f["finger"])[1]) for f in spec["fingers"]}
    raise KeyError(ref)


def place(board, stage, spec, part, sch_fields=None, sch_nc=None):
    ref = part["ref"]
    fpl = spec["floorplan"]
    pl = fpl["placements"].get(ref) or fpl["placements_other"][ref]
    fp = load_footprint(part["footprint"])
    fp.SetFPIDAsString(part["footprint"])
    fp.SetReference(ref)
    fp.SetValue(part["value"])
    board.Add(fp)
    fp.SetPosition(C.c2k(pl["x"], pl["y"]))
    if pl["side"] == "B":
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    want = expected_pads(spec, ref)
    # orientation: the spec gives rot_deg for placements_other; the 68 field parts give pad centres only.
    cands = [pl["rot_deg"]] if "rot_deg" in pl else [0, 90, 180, 270]
    ok = None
    for rot in cands:
        fp.SetOrientationDegrees(rot)
        err = 0.0
        for p in fp.Pads():
            n = p.GetNumber()
            if n in want and p.IsOnCopperLayer():
                x, y = C.k2c(p.GetPosition())
                if n in ("11",) and ref == "U2":           # exposed pad tabs share the number: compare the centre pad only
                    if abs(x - pl["x"]) > 1e-6 or abs(y - pl["y"]) > 1e-6:
                        continue
                err = max(err, abs(x - want[n][0]), abs(y - want[n][1]))
        if err < 2e-3:
            ok = rot
            break
    if ok is None:
        raise RuntimeError("%s: no orientation reproduces the pad positions of the spec (%s)" % (ref, cands))
    # nets
    for p in fp.Pads():
        n = p.GetNumber()
        if n == "":
            continue
        if n not in part["pins"] and not (n.isdigit() and int(n) in part["pins"]):
            raise RuntimeError("%s: footprint pad %s is not in the spec" % (ref, n))
        netname = part["pins"][n]
        if netname:
            p.SetNet(C.net(board, netname))
        elif sch_nc and (ref, n) in sch_nc:
            # an unconnected pin: the net name KiCad derives for it in the schematic, so that the native schematic <-> board
            # comparison finds the same net on the pad (nothing is ever routed on it)
            nm = sch_nc[(ref, n)]
            ni = board.FindNet(nm)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(board, nm)
                board.Add(ni)
            p.SetNet(ni)
    # attributes and fields (the fields of the schematic symbol: MPN, Manufacturer, Datasheet, Description)
    fp.SetExcludedFromBOM(not part.get("in_bom", True))
    fp.SetDNP(bool(part.get("dnp")))
    want = {"MPN": part.get("mpn", ""), "Manufacturer": part.get("manufacturer", ""), "Description": part.get("description", ""), "Datasheet": ""}
    want.update((sch_fields or {}).get(ref, {}))
    for f in fp.GetFields():
        if f.GetName() in ("Datasheet", "Description"):
            f.SetText(want[f.GetName()])
            f.SetVisible(False)
    for name in ("MPN", "Manufacturer"):
        if want[name] is not None:
            fp.SetField(name, want[name])
    for f in fp.GetFields():
        if f.GetName() in ("MPN", "Manufacturer"):
            f.SetVisible(False)
    for m in fp.Models():
        m.m_Filename = m.m_Filename.replace("${KICAD8_3DMODEL_DIR}", "${KICAD10_3DMODEL_DIR}")
    return fp, ok


def restyle_text(t, h, thick):
    t.SetTextSize(pcbnew.VECTOR2I(C.IU(h), C.IU(h)))
    t.SetTextThickness(C.IU(thick))


def silk_and_fab(board, spec, fps):
    """Reference texts: small, on silk only where a spot clear of every pad and courtyard exists.
    Dense parts (the divider / tap / termination fields, everything under U1) keep the reference on Fab only."""
    from shapely.geometry import box
    fpl = spec["floorplan"]
    u1b = fpl["U1"]["body"]
    dense = set(fpl["placements"]) | {r for r, pl in fpl["placements_other"].items() if pl["side"] == "B"
                                      and (u1b["x"][0] - 1.0 <= pl["x"] <= u1b["x"][1] + 1.0 or pl["y"] < 9.0)}
    obstacles = {"F": [], "B": []}
    for ref, fp in fps.items():
        side = "B" if fp.IsFlipped() else "F"
        for p in fp.Pads():
            for s_, lid in (("F", pcbnew.F_Cu), ("B", pcbnew.B_Cu)):
                if p.IsOnLayer(lid):
                    bb = p.GetBoundingBox()
                    x0, y0 = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom()))
                    x1, y1 = C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
                    obstacles[s_].append(box(x0, y0, x1, y1).buffer(0.10))
        cy = fpl["placements_other"].get(ref, {}).get("courtyard")
        if cy and ref not in ("J1",):
            obstacles[side].append(box(*cy))
    placed = {"F": [], "B": []}
    hidden, shown = [], []
    for ref, fp in fps.items():
        side = "B" if fp.IsFlipped() else "F"
        rf = fp.Reference()
        val = fp.Value()
        restyle_text(val, 0.7 if ref == "J1" else FAB_TEXT_H, 0.1 if ref == "J1" else FAB_TEXT_T)
        if ref != "J1":
            val.SetVisible(False)                           # values: BOM / fields only (test pads show theirs on silk)
        for g in fp.GraphicalItems():
            if g.Type() == pcbnew.PCB_TEXT_T and g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab) and "REFERENCE" in g.GetText():
                small = ref not in ("U1", "U2", "U3", "U4", "U5")
                restyle_text(g, FAB_TEXT_H if small else 0.6, FAB_TEXT_T if small else 0.09)
                g.SetPosition(fp.GetPosition())
        if ref == "J1":
            continue                                        # reference hidden in the footprint; pin-number marks stay
        if ref in dense:
            rf.SetVisible(False)
            hidden.append(ref)
            continue
        if ref.startswith("FID"):                           # fiducials: no silk at all (nothing but the dot inside the mask opening)
            rf.SetVisible(False)
            rf.SetLayer(pcbnew.B_Fab if side == "B" else pcbnew.F_Fab)
            hidden.append(ref)
            continue
        text = part_silk_text(spec, ref)
        if ref.startswith("TP"):                            # the rail name (Value) goes on silk, the reference stays on Fab
            rf.SetVisible(False)
            rf = val
            rf.SetVisible(True)
            rf.SetLayer(pcbnew.F_SilkS)
        restyle_text(rf, SILK_TEXT_H, SILK_TEXT_T)
        rf.SetTextAngleDegrees(0)
        rf.SetKeepUpright(True)
        w = 1.0 * SILK_TEXT_H * len(text) + 0.2
        h = SILK_TEXT_H + 0.15
        cy = fpl["placements_other"][ref]["courtyard"]
        cx, cyy = (cy[0] + cy[2]) / 2, (cy[1] + cy[3]) / 2
        g = 0.12
        cands = [(cx, cy[3] + g + h / 2), (cx, cy[1] - g - h / 2), (cy[0] - g - w / 2, cyy), (cy[2] + g + w / 2, cyy),
                 (cy[0] + w / 2, cy[3] + g + h / 2), (cy[2] - w / 2, cy[3] + g + h / 2),
                 (cy[0] + w / 2, cy[1] - g - h / 2), (cy[2] - w / 2, cy[1] - g - h / 2)]
        if ref == "U1":
            cands = [(cy[0] + w / 2 + 1.6, cy[3] + g + h / 2)] + cands
        spot = None
        for x, y in cands:
            b = box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
            own = box(*cy)
            if y - h / 2 < 3.0 or y + h / 2 > 29.6 or x - w / 2 < 0.5 or x + w / 2 > 69.1:
                continue
            if any(b.intersects(o) for o in obstacles[side] if not o.equals(own)) or any(b.intersects(o) for o in placed[side]):
                continue
            spot = (x, y, b)
            break
        if spot is None:
            rf.SetVisible(False)
            hidden.append(ref)
            continue
        rf.SetPosition(C.c2k(spot[0], spot[1]))
        rf.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
        rf.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        placed[side].append(spot[2])
        shown.append(ref)
    return shown, hidden


def part_silk_text(spec, ref):
    """Test pads are labelled with their rail."""
    if ref.startswith("TP"):
        return next(p["value"] for p in spec["parts"] if p["ref"] == ref)
    return ref


def set_uuids(stage, fp):
    stage.tag(fp, "footprint")
    for f in fp.GetFields():
        stage.tag(f, "fpitem")
    for g in fp.GraphicalItems():
        stage.tag(g, "fpitem")
    for p in fp.Pads():
        stage.tag(p, "fpitem")
    for z in fp.Zones():
        stage.tag(z, "fpitem")


def main():
    spec = C.spec()
    ncls, npat, kept = write_project(spec)
    nrules = write_dru(spec)
    write_fp_lib_table(spec)
    C.log(STAGE, "project file: %d net classes, %d net patterns%s; %d custom rules; fp-lib-table written"
          % (ncls, npat, " (schematic sections kept)" if kept else "", nrules))

    with open(C.BOARD, "w") as fh:
        fh.write(board_header(spec))
    board = C.load_board()
    stage = C.Stage(board, STAGE)

    for n in C.nets():
        ni = pcbnew.NETINFO_ITEM(board, n)
        board.Add(ni)
    C.log(STAGE, "%d nets" % len(C.nets()))

    sch_fields, sch_nc = schematic_export()
    C.log(STAGE, "schematic parity data: fields of %d symbols, %d unconnected-pin nets%s"
          % (len(sch_fields), len(sch_nc), "" if sch_fields else " (no schematic found: the pads of unconnected pins stay without a net)"))
    fps = {}
    order = sorted(spec["parts"], key=lambda p: (p["ref"] != "J1", p["ref"] != "U1", p["ref"]))
    for part in order:
        fp, rot = place(board, stage, spec, part, sch_fields, sch_nc)
        fps[part["ref"]] = fp
    shown, hidden = silk_and_fab(board, spec, fps)
    for part in order:
        set_uuids(stage, fps[part["ref"]])

    # board identification text (no logo, no branding): name, revision and a date placeholder that the fab house / assembler does not fill in
    t = pcbnew.PCB_TEXT(board)
    t.SetText((BOARD_ID % spec["meta"]["revision"]) + "   DATE ____-__-__")
    t.SetLayer(pcbnew.F_SilkS)
    t.SetPosition(C.c2k(46.0, 28.7))
    restyle_text(t, 0.8, 0.12)
    stage.tag(t, "text")
    board.Add(t)
    # fabrication notes (Cmts.User): what the Gerbers cannot say
    st = spec["stackup"]
    fpl = spec["floorplan"]
    n_vip = len(fpl["U1"]["via_in_pad"]["balls"])
    n_node = sum(1 for v in fpl["vias"] if v.get("fill") == "filled and capped")
    ep_vias = sorted((v["x"], v["y"]) for v in (fpl["power"].get("build") or {}).get("vias", []) if v.get("in_pad"))
    notes = ["FABRICATION NOTES   %s   rev %s" % (BOARD_ID % spec["meta"]["revision"], spec["meta"]["revision"]),
             "1. 6-layer FR-4 (Tg >= 170 C).  FINISHED THICKNESS MEASURED OVER THE GOLD FINGERS 1.20 +/-0.10 mm (not +/-10 %).",
             "2. STACKUP, top to bottom (mm):"]
    for lay in st["layers"]:
        if lay["type"] == "copper":
            notes.append("      %-7s copper %.3f%s" % (lay["layer"], lay["thickness_mm"], "  (finished, with plating: 0.040 nominal)" if lay["layer"] in ("F.Cu", "B.Cu") else ""))
        elif lay["type"] == "soldermask":
            notes.append("      %-7s solder mask %.3f (0.020 over copper), removed over the fingers" % (lay["layer"], lay["thickness_mm"]))
        else:
            notes.append("              %s %s %.3f, er %.2f" % (lay["type"], lay["material"], lay["thickness_mm"], lay["er"]))
    notes += [
        "      total %.3f with mask, %.3f at the bare fingers, %.2f with hard gold." % (st["total_thickness_with_mask_mm"], st["thickness_at_fingers_no_mask_mm"],
                                                                                    st["thickness_at_fingers_with_hard_gold_mm"]),
        "3. CONTROLLED IMPEDANCE, all +/-10 %; widths may be adjusted by +/-0.02 mm:",
        "      In3.Cu         0.15 mm single (ref In2 / In4)            46 ohm",
        "      In3.Cu         0.10 / 0.10 mm pair                       85 ohm differential",
        "      F.Cu / B.Cu    0.20 mm single (ref In1 / In4)            46 ohm",
        "      F.Cu / B.Cu    0.15 mm single                            52 ohm",
        "      F.Cu / B.Cu    0.10 / 0.10 mm pair (WCK on B.Cu, RDQS drop on F.Cu)   92 ohm differential",
        "4. FINISH: fingers (J1 pads 1 .. 260) %s." % st["copper_finish"].replace("hard gold over nickel on the fingers", "hard gold over nickel").replace(", ENIG elsewhere", "; all other exposed copper ENIG"),
        "5. CARD EDGE: %s.  No solder mask and no copper pour over the fingers; no plating tie bars left on the card." % st["edge"],
        "6. VIA-IN-PAD, FILLED AND CAPPED (IPC-4761 type VII), %d vias: the %d vias 0.40 / 0.15 mm in the lands of U1, the %d command node vias 0.40 / 0.20 mm (row"
        % (n_vip + n_node + len(ep_vias), n_vip, n_node),
        "   y = 5.85 mm, x = 16.325 + 0.8 k mm, k = 0 .. 9, from the pin-1 corner) and the %d vias 0.45 / 0.20 mm in the exposed pads of U2, U4, U5 at (x, y) mm:" % len(ep_vias),
        "   " + "  ".join("(%.2f, %.2f)" % q for q in ep_vias) + ".",
        "   The 0.20 mm filled holes share their drill size with plain vias: they are the ones listed here (the board file carries the filled / capped attribute per via).",
        "   All other vias are plain and tented on both sides.",
        "7. UNUSED INNER-LAYER PADS ARE REMOVED IN THE DATA on the via-in-pads of U1, the command fence vias, the node vias of the data side (row y = 6.00 mm),",
        "   the shunt, read, return and reference vias, the finger vias and every via of the power stage: do not add them back.  The ten command node vias",
        "   (row y = 5.85 mm) keep their pads on every layer.  Hole to copper 0.20 mm is used at its limit beside the RDQS pair inside U1 and at its two",
        "   return vias above fingers 219 / 221 (0.225 mm).",
        "8. Minimum track 0.09 mm (inside U1 only, 0.10 mm elsewhere), minimum gap 0.10 mm, minimum drill 0.15 mm (via-in-pad), 0.20 mm elsewhere; annular ring 0.10 mm.",
        "9. OUTER-LAYER COPPER DENSITY IS LOW BY DESIGN: F.Cu and B.Cu carry tracks, pads and a few small power pours under U1 (about 16 to 17 % of the card area per",
        "   face, a third of it the finger row); there is NO ground flood.  DO NOT ADD COPPER THIEVING, DUMMY FILL OR ANY POUR inside the card outline: the outer-layer",
        "   lines are impedance-controlled against In1 / In4 without coplanar copper.  Balance the plating with robber bars on the panel border; the inner layers",
        "   In1 / In2 / In4 are solid planes (more than 90 % copper), In3 is a sparse signal layer between them.",
        "10. ACCEPTANCE: IPC-6012 class 2 (annular ring 0.10 mm on the 0.20 mm drills: class 3 is not attainable).  100 % bare-board netlist test against the",
        "   IPC-D-356 netlist.  Bow and twist <= 0.5 %, flatness over the finger row <= 0.15 mm.  Cap plating of the filled vias flush within +/-0.025 mm.",
        "   TDR coupons for the impedance classes of note 3 on the panel border, with outer-layer copper density like the card (no flood).",
        "11. PANEL: the card is 30 mm high and must ship in a panel.  Rout the outline; break-off tabs on the top edge or on the sides above the latch",
        "   notches only (y > 22 mm), none on the finger edge or in the notches; no V-score.  Panel rails carry three fiducials per face.",
        "12. SOLDER MASK: LPI, 0.05 mm expansion in the data; colour as specified (black) or green if the 0.08 mm dam between the two lands of an 0201",
        "   cannot be held in black; where a dam cannot be held, the two lands of one part may share a window.  The mask-free finger strip ends at",
        "   y = 2.75 mm; the row of vias 0.15 mm above the finger tops (y = 3.05 mm, tented, 0.10 mm dam) is intentional: do not move or remove them.",
        "13. ASSEMBLY: three fiducials per face on the card (FID1 .. FID6: 1.0 mm dot, 2.0 mm mask opening).  0.10 mm stencil; no paste on the fingers,",
        "   test pads and fiducials.  Tallest part 1.10 mm on either face (module limit 1.20 mm per face)."]
    for i, line in enumerate(notes):
        tn = pcbnew.PCB_TEXT(board)
        tn.SetText(line)
        tn.SetLayer(pcbnew.Cmts_User)
        tn.SetPosition(C.c2k(0.0, -4.0 - 1.5 * i))
        tn.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
        restyle_text(tn, 0.9, 0.11)
        stage.tag(tn, "text")
        board.Add(tn)

    ol = C.board_polygon(board)
    if len(ol) != 1 or len(ol[0].interiors) != 0:
        raise RuntimeError("Edge.Cuts must be one closed outline without cut-outs, got %d outlines" % len(ol))
    C.save_board(board)
    nf = sum(1 for f in board.GetFootprints())
    n_ref = sum(1 for r in shown if not r.startswith("TP"))
    C.log(STAGE, "%d footprints placed (%d front, %d back); on silk: %d references and %d test-pad rail names; reference on Fab only: %d"
          % (nf, sum(1 for f in board.GetFootprints() if not f.IsFlipped()),
             sum(1 for f in board.GetFootprints() if f.IsFlipped()), n_ref, len(shown) - n_ref, len(hidden)))
    b = ol[0].bounds
    C.log(STAGE, "outline: one closed contour, %.3f x %.3f mm, area %.1f mm2" % (b[2] - b[0], b[3] - b[1], ol[0].area))


if __name__ == "__main__":
    main()
