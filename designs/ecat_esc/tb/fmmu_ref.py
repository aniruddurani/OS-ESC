"""Python reference for the FMMU plan and lookup, with the semantics of the original
single-cycle fmmu.v: an FMMU contributes to a logical datagram if it is active, has a
nonzero length, overlaps the datagram, allows its direction, and the mapped chunk fits
the registers or the RAM. For each byte offset the lowest-numbered contributing FMMU of
each direction owns the byte."""
import random

MASK17 = 0x1FFFF


def pack_cfg(fmmus):
    """fmmus: list of dicts (start, len, phys, rd, wr, act) -> the 128-bit-per-FMMU bus."""
    value = 0
    for i, f in enumerate(fmmus):
        word = f["start"] & 0xFFFFFFFF
        word |= (f["len"] & 0xFFFF) << 32
        word |= (f["phys"] & 0xFFFF) << 64
        word |= (1 if f["rd"] else 0) << 88
        word |= (1 if f["wr"] else 0) << 89
        word |= (1 if f["act"] else 0) << 96
        value |= word << (128 * i)
    return value


def plan_one(f, ds, dl, is_rd, is_wr, ram_bytes):
    """-> (hr, hw, seg_start, seg_end, seg_phys); segment fields only mean something
    when hr or hw is set."""
    ws = f["start"]
    we = ws + f["len"]
    de = ds + dl
    lo = max(ds, ws)
    hi = min(de, we)
    ovl = bool(f["act"]) and f["len"] != 0 and lo < hi
    off = (lo - ws) & 0xFFFF
    cl = (hi - lo) & 0x7FF
    plo = f["phys"] + off
    phi = (plo + cl) & MASK17
    ram_end = (phi - 0x1000) & MASK17
    fits = phi <= 0x1000 or (plo >= 0x1000 and ram_end <= ram_bytes)
    hr = ovl and fits and bool(f["rd"]) and is_rd
    hw = ovl and fits and bool(f["wr"]) and is_wr
    seg_start = (lo - ds) & 0x7FF
    return hr, hw, seg_start, (seg_start + cl) & 0x7FF, plo & 0xFFFF


def plan(fmmus, ds, dl, is_rd, is_wr, ram_bytes):
    return [plan_one(f, ds, dl, is_rd, is_wr, ram_bytes) for f in fmmus]


def walk(segs, n):
    """-> (rd_hit, rd_addr, wr_hit, wr_addr) for datagram byte offset n; segs as returned
    by plan(). Addresses are 0 when there is no hit."""
    out = []
    for want in (0, 1):
        hit, addr = False, 0
        for hr, hw, s0, s1, ph in segs:
            if (hr, hw)[want] and s0 <= n < s1:
                hit, addr = True, (ph + (n - s0)) & 0xFFFF
                break
        out += [hit, addr]
    return tuple(out)


def random_fmmus(rng, count, ds, ram_bytes):
    """FMMU configs that often overlap a datagram at ds, with physical areas around the
    register/RAM boundary and the end of the RAM."""
    fmmus = []
    for _ in range(count):
        near = rng.random() < 0.8
        start = (ds + rng.randint(-300, 300)) & 0xFFFFFFFF if near else rng.getrandbits(32)
        phys = rng.choice([rng.randint(0, 0x1FFF), 0x1000 + rng.randint(0, ram_bytes),
                           0x1000 + ram_bytes - rng.randint(0, 40), 0x1000 - rng.randint(0, 40)])
        fmmus.append({"start": start, "len": rng.choice([0, rng.randint(1, 600), rng.randint(1, 20)]),
                      "phys": phys, "rd": rng.random() < 0.7, "wr": rng.random() < 0.7,
                      "act": rng.random() < 0.9})
    return fmmus


def random_datagram_addr(rng):
    return rng.choice([rng.getrandbits(32), rng.randint(0, 0x2000), 0xFFFFFF00 + rng.randint(0, 255)])
