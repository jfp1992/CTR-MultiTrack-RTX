"""Nitro-Fueled remaster tracks: each stock PS1 level (slot) and the Nitro-Fueled
archive that remasters it, the Nitro-Fueled only tracks (Crash Nitro Kart's)
that a fan made PS1 remake of is installed for (HOSTED), and the Grand Prix
tracks played on their own race data over a stock host slot (NATIVE).

They are extra entries in the track list, named "NF <name>": the game plays
the stock level (its own bigfile entries, so the disc grows by nothing) or the
remake (tracks/<id>, its entries again; a native track: its host slot's level,
with nf_race.py's start grid, restart path and AI paths) and the native ray
tracer draws the
Nitro-Fueled level over it (renderer key "nf-<slot>" / "nf-<id>",
assets/rtx_remaster/<key>.rtxm). The stock and custom entries stay as they
are. Only offered when the user's own Nitro-Fueled package is unpacked
(scripts/nf_setup.sh) and only on the native build (MT_NF=1).

A track id (tid) is the stock slot (int) or a hosted / native track's short id
(str).
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCHIVES = pathlib.Path(os.environ.get("NF_ARCHIVES", ROOT / "nf_raw/pkg/uroot/archives"))
UPDATE = ROOT / "nf_raw/pkg_update/uroot/archives"
# the update's own levels (the Grand Prix tracks) come nested in its update*.pak;
# unnest_update() takes them out to here
UPDATE_LEVELS = ROOT / "nf_raw/pkg_update/levels"
# where archives are looked for, the game's first: the stock NF tracks keep the
# game package's levels (the update's patched files are never used for them)
ARCHIVE_DIRS = [ARCHIVES, UPDATE_LEVELS]

# PS1 scenery the remaster has its own of (Model.id; -1 = the levels' static
# decoration models), never drawn on NF tracks. Gameplay models stay.
HIDE = [166, -1]  # startbanner, static decoration

# slot (levelID), Nitro-Fueled archive, extra hidden model ids
LEVELS = [
    (0, "t134_dingo_canyon", []),
    (1, "t142_dragon_mines", [33]),  # minecart (PROXIES)
    (2, "t141_blizzard_bluff", []),
    (3, "t111_crash_cove", []),
    (4, "t132_tiger_temple", []),
    (5, "t133_papus_pyramid", []),
    (6, "t112_roos_tubes", []),
    (7, "t153_hot_air_skyway", [37]),  # blades: the zeppelins' own propellers turn
    (8, "t114_sewer_speedway", []),
    (9, "t113_mystery_caves", []),
    (10, "t152_cortex_castle", [91]),  # castlesign
    (11, "t151_ngin_labs", []),
    (12, "t143_polar_pass", []),
    (13, "t154_oxide_station", []),
    (14, "t131_coco_park", [74]),  # sishead
    (15, "t144_tiny_arena", []),
    (16, "t121_slide_coliseum", []),
    (17, "t122_turbo_track", []),
    (18, "a155_nitro_court", []),
    (19, "a135_rampage_ruins", []),
    (20, "a191_parking_lot", []),
    (21, "a115_skull_rock", []),
    (22, "a192_the_north_bowl", []),
    (23, "a145_rocky_road", []),
    (24, "a193_lab_basement", []),
]

FIRST_BATTLE_SLOT = 18

# Nitro-Fueled only tracks: short id, archive, display name, the remake that
# hosts it (tracks/<id>: its lev is played), extra hidden ids. A remake whose
# layout doesn't match the Nitro-Fueled one closely enough is left out at build
# (nf_level.solve_similar). No remake: no entry (Tiny Temple, Clockwork Wumpa,
# Hyper Spaceway, the CNK battle arenas); the Grand Prix tracks are NATIVE.
HOSTED = [
    ("t211", "t211_inferno_island", "Inferno Island", "inferno-island", []),
    ("t212", "t212_jungle_boogie", "Jungle Boogie", "n-gage-jungle-boogie", []),
    ("t221", "t221_meteor_gorge", "Meteor Gorge", "meteor-gorge", []),
    ("t222", "t222_barin_ruins", "Barin Ruins", "n-gage-barin-ruins", []),
    ("t223", "t223_deep_sea_driving", "Deep Sea Driving", "n-gage-deep-sea-driving", []),
    ("t231", "t231_out_of_time", "Out of Time", "n-gage-out-of-time", []),
    ("t233", "t233_thunder_struck", "Thunderstruck", "nf-thunderstruck", []),
    ("t241", "t241_assembly_lane", "Assembly Lane", "n-gage-assembly-lane", []),
    ("t242", "t242_android_alley", "Android Alley", "n-gage-android-alley", []),
    ("t243", "t243_electron_avenue", "Electron Avenue", "nf-electron-avenue", []),
]

# Nitro-Fueled tracks played on Nitro-Fueled's own race data (start grid,
# restart path, AI nav, crates: scripts/nf_race.py, in their .coll) over a stock
# host slot (its music and gameplay models): short id, archive, display name,
# host slot (music, the crate / fruit models). Twilight Tour and Drive-Thru
# Danger have fan remakes too, but play on their own data here.
NATIVE = [
    ("t155", "t155_sony_stadium", "Retro Stadium", 17),            # Turbo Track
    ("t501", "t501_july_world_tour", "Twilight Tour", 14),         # Coco Park
    ("t502", "t502_back_in_time", "Prehistoric Playground", 4),    # Tiger Temple
    ("t503", "t503_september_spyro", "Spyro Circuit", 7),          # Hot Air Skyway
    ("t504", "t504_october_spooky", "Nina's Nightmare", 10),       # Cortex Castle
    ("t505", "t505_nitro_festival", "Koala Carnival", 16),         # Slide Coliseum
    ("t506", "t506_luminous", "Gingerbread Joyride", 12),          # Polar Pass
    ("t507", "t507_dry_land", "Megamix Mania", 0),                 # Dingo Canyon
    ("t508", "t508_toxic", "Drive-Thru Danger", 8),                # Sewer Speedway
]


def native(tid):
    """NATIVE entry of a track id, or None"""
    return next((n for n in NATIVE if n[0] == tid), None)


def native_host(tid):
    """the stock slot a native track is played over"""
    return native(tid)[3]


# models a level's scripts place (game entities nf_level doesn't read), stored
# in world space: archive -> model file globs under its models/ (Koala
# Carnival's tunnel turbo pads, lit: their "_Off" twins left out)
WORLD_MODELS = {
    "t505_nitro_festival": ["Levels/Octane/T505_Nitro_Festival/T505_Tunnel_BoostPad_[0-9]*.igz"],
}


# animated models played faster than Nitro-Fueled has them: slot -> {model file
# name prefix: speed}
ANIM_SPEED = {
    7: {"T153_Zeppelin_01": 4.0, "T153_Rotor_Solo": 4.0},  # Hot Air Skyway's propellers
}

# PS1 models that move (their thread animates the instance) drawn as Nitro-Fueled
# models where the game has them each frame: slot -> [(Model.id, NF model per
# instance (the n-th instance the game reports gets the n-th), NF draws left
# out, scale (the NF model's size in its level: its entities' scale), headlight
# or None: (NF model space position, colour, falloff, size))]
PROXIES = {
    1: [(33, ["T142_Dragon_Mines_Minecart_moving_coal", "T142_Dragon_Mines_Minecart_moving_gold", "T142_Dragon_Mines_Minecart_moving_empty"],
         ["BonusWarp_glow"],  # (NF's light cone: a real light instead)
         4.0,  # (Dragon Mines' own cart props: x2.1 - x4.6, x4 by the track)
         ((80.0, 0.0, 104.0), (4.0, 3.9, 3.5), 400.0, 20.0))],
}


# every NF track: its karts collide with the Nitro-Fueled level's own (Havok)
# collision, not the PS1 level's quadblocks (scripts/nf_collision.py ->
# <key>.coll, loaded by native/game/MultiTrack/MT_NfColl.c)
def collision_tracks():
    return [tid for tid, _, _ in available()]


def key(tid):
    return f"nf-{tid}"


def pak_path(pak):
    """the archive <pak>.pak in the first of ARCHIVE_DIRS that has it, or None"""
    for d in ARCHIVE_DIRS:
        for name in (pak + ".pak", pak.title() + ".pak"):
            if (d / name).exists():
                return d / name
    return None


def hosted(tid):
    """HOSTED entry of a hosted track id, or None (a stock slot)"""
    return next((h for h in HOSTED if h[0] == tid), None)


def host_lev(tid):
    """path of the PS1 level the track plays: the stock level or the remake's
    (a native track has none: its host slot's, native_host, is only loaded)"""
    h = hosted(tid)
    if h:
        return ROOT / "tracks" / h[3] / "track.lev"
    if native(tid):
        raise ValueError(f"{tid}: a native NF track has no PS1 level (host slot {native_host(tid)})")
    return ROOT / f"stock_tracks/{tid:02d}-0/track.lev"


def is_battle(tid):
    return isinstance(tid, int) and tid >= FIRST_BATTLE_SLOT


def hide_ids(extra):
    return (HIDE + list(extra))[:8]


def available():
    """(tid, archive, extra hidden ids) of every NF track whose archive is
    there (and, hosted ones, whose remake is installed)"""
    out = [lv for lv in LEVELS if pak_path(lv[1])]
    out += [(h[0], h[1], h[4]) for h in HOSTED if pak_path(h[1]) and host_lev(h[0]).exists() and not placement_bad(h[0])]
    out += [(n[0], n[1], []) for n in NATIVE if pak_path(n[1])]
    return out


def placement_bad(tid):
    """whether the remake's layout was found not to match the Nitro-Fueled
    level (nf_level.placement: "bad" in scripts/nf_placement.json or the
    nf_raw/placement.json cache)"""
    import json
    for f in (ROOT / "scripts/nf_placement.json", ROOT / "nf_raw/placement.json"):
        if f.exists():
            e = json.loads(f.read_text()).get(str(tid))
            if e:
                return bool(e.get("bad"))
    return False
