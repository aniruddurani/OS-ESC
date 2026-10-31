# Design: open-source silicon flow, milestone 1 (sky130 inverter)

Date: 2026-10-31
Status: draft for review

## 1. Purpose

Build a repository that runs an open-source IC design flow from the
command line, as a replacement for a Cadence Virtuoso-style workflow. The
long-term goal is to learn the flow and work toward a real tapeout on an
open shuttle (Tiny Tapeout SKY via ChipFoundry, or gf180mcu via wafer.space).

The flow will eventually cover both analog/custom and digital design. This
spec covers only the first milestone: an analog/custom inverter taken end to
end on sky130, with every stage scripted, gated and reported in a
machine-readable form.

### Decisions already made

| Topic | Decision |
|---|---|
| Goal | Learn the flow now, build toward a real tapeout later |
| Design type | Both analog and digital eventually; analog inverter milestone first |
| PDK | sky130A, as shipped in the pinned container image |
| Environment | Docker Desktop (WSL2 backend) + IIC-OSIC-TOOLS image |
| Tool location | Windows host; tools run in the container |
| Repo location | `C:\Users\aniru\silicon-flow` (outside OneDrive) |

## 2. Success criteria

The milestone is done when, from a fresh clone on this machine:

1. `./flow up` starts the container.
2. `./flow all` passes every stage for the inverter (netlist, sim, layout,
   DRC, LVS, PEX) and writes `build/inv/reports/summary.json` with
   `"status": "pass"`.
3. `./flow selftest` passes, which means both known-bad layouts fail the gate
   they are built to fail.
4. `./flow png` writes a rendered image of the layout.
5. `docs/FLOW.md` documents how to do all of the above, the flow order and
   the rules in section 7.

## 3. Environment

### 3.1 Host prerequisites (done by the user, need admin rights)

- Hypervisor enabled: `bcdedit /set hypervisorlaunchtype auto`, and the
  `VirtualMachinePlatform` Windows feature enabled, followed by a reboot.
  (TwinCAT runtime needs the hypervisor off. Switching between the two is
  `hypervisorlaunchtype off|auto` plus a reboot.)
- Docker Desktop installed with the WSL2 backend; its terms accepted by the
  user on first launch.
- Free disk: about 58 GB free on C: at the time of writing. The image is
  about 4 GB compressed and larger once unpacked. After the pull, at least
  15 GB must remain free; if not, stop and ask the user before continuing.

### 3.2 Container

- `compose.yaml` pins `hpretl/iic-osic-tools:2026.10`. The tag fixes the
  versions of every tool and of the sky130A PDK. Upgrading the tag is a
  deliberate change, made in its own commit after `./flow all` and
  `./flow selftest` pass on the new tag.
- One long-lived container named `sf-iic`, started headless (no VNC
  desktop). The image's entrypoint behaviour for headless use is verified
  during implementation and recorded in `docs/FLOW.md`.
- The repo is bind-mounted at `/foss/designs/silicon-flow`.
- PDK: `PDK_ROOT` and `PDK=sky130A` as set by the image. The exact paths of
  the Magic rc file, Netgen setup file, KLayout DRC deck, KLayout layer
  properties and ngspice model library are looked up inside the image during
  implementation and recorded in the `Makefile` and `docs/FLOW.md`.

### 3.3 The `flow` wrapper

`flow` is a bash script run from Git Bash on Windows. It is the only command
used to run tools.

| Command | Action |
|---|---|
| `./flow up` | `docker compose up -d`; waits until the container is running |
| `./flow down` | `docker compose down` |
| `./flow shell` | Interactive shell in the container (for the user) |
| `./flow <args...>` | Starts the container if needed, then runs `docker exec sf-iic make -C /foss/designs/silicon-flow <args...>` |

- Arguments go straight to `make`, so variables work: `./flow sim DESIGN=inv`.
- The exit code of `make` is the exit code of `flow`.
- The wrapper sets `MSYS_NO_PATHCONV=1` so that Git Bash does not rewrite
  container paths such as `/foss/...` into Windows paths.

### 3.4 Line endings

Git for Windows on this machine has `core.autocrlf=true` at system level.
`.gitattributes` sets `* text=auto eol=lf`, so Tcl, SPICE, Makefiles and
shell scripts are always checked out with LF endings and run correctly in
the Linux container.

## 4. Repository layout

```
silicon-flow/
  flow                      # wrapper (section 3.3)
  compose.yaml              # pinned image, mount, container name
  Makefile                  # DESIGN ?= inv, PDK ?= sky130A, one target per stage
  docs/FLOW.md              # how to run, flow order, rules (section 7)
  .gitattributes            # eol=lf
  .gitignore                # build/
  scripts/                  # shared helpers: report writers/parsers (Python),
                            # Magic and Netgen Tcl, KLayout render script
  designs/inv/
    xschem/inv.sch          # inverter schematic
    xschem/inv.sym          # inverter symbol
    tb/inv_tb.spice         # testbench: sources, load, analyses, measurements
    layout/inv.tcl          # Magic script that generates the layout
    spec.json               # pass criteria for sim and PEX
  tests/fixtures/
    inv_drc_bad.tcl         # layout with a deliberate spacing violation
    inv_lvs_bad.tcl         # DRC-clean layout with a missing connection
  build/<design>/           # generated, gitignored: netlists, GDS, logs, reports
  docs/specs/   # this spec
```

Layout and testbench are text files so they can be diffed, reviewed and
regenerated. Nothing in `build/` is edited by hand.

## 5. Flow stages

Each stage is a `make` target. Each writes
`build/<design>/reports/<stage>.json` and exits non-zero unless its status is
`pass`. Targets declare their inputs as prerequisites, so `make` stops at the
first failing stage and reruns only what changed.

| Target | Inputs | Tool | Output | Pass criteria |
|---|---|---|---|---|
| `netlist` | `inv.sch`, `inv.sym` | Xschem, batch | `build/inv/inv.spice` (subcircuit `inv`) | Netlist file written, netlister reports no errors |
| `sim` | netlist, `inv_tb.spice`, `spec.json` | ngspice, batch | `reports/sim.json` | Every measurement in `spec.json` present and within limits |
| `layout` | `inv.tcl` | Magic, headless | `build/inv/inv.mag`, `build/inv/inv.gds` | GDS written; ports `in`, `out`, `vdd`, `vss` present |
| `drc` | GDS / mag | Magic DRC (full rule style) and the PDK's KLayout DRC deck | `reports/drc.json` | 0 violations from Magic and 0 from KLayout |
| `lvs` | layout, netlist | Magic extraction, then Netgen with the PDK setup file | `reports/lvs.json` | Netgen's final result reports a unique match, with no pin or property mismatches |
| `pex` | layout (LVS passed), testbench, `spec.json` | Magic extraction with parasitic capacitance, then ngspice | `build/inv/inv_pex.spice`, `reports/pex.json` | Every measurement still within limits; report lists schematic vs extracted values |
| `png` | GDS | KLayout, batch, PDK layer colours | `build/inv/inv.png` | Not a gate |
| `all` | | runs `netlist sim layout drc lvs pex` in order | `reports/summary.json` | All six stages pass |
| `selftest` | fixtures | see 6.3 | `build/selftest/` | See 6.3 |
| `clean` | | | removes `build/<design>/` | |

### 5.1 Inverter

- Devices: `sky130_fd_pr__nfet_01v8` and `sky130_fd_pr__pfet_01v8`,
  L = 0.15 um. Starting widths: nfet 1 um, pfet 2 um. Widths may be tuned
  during implementation to meet `spec.json`; the chosen values are recorded
  in the schematic and layout script.
- Supply 1.8 V, TT corner, 27 C.
- Layout: devices placed with the PDK's parametric device generators in
  Magic, wired in local interconnect and metal 1, with an n-well tap tied
  to `vdd` and a substrate tap tied to `vss`. Ports `in`, `out`, `vdd`, `vss`
  are labelled on metal 1.

### 5.2 Simulation and pass criteria

The testbench does not hard-code the device-under-test netlist. The
`Makefile` generates a short top-level deck that includes the DUT netlist
(schematic netlist for `sim`, extracted netlist for `pex`) and then
`inv_tb.spice`. Both runs therefore use the same stimuli and measurements.

The testbench drives `in` with a pulse, loads `out` with 10 fF, and runs a DC
sweep of `in` plus a transient. ngspice writes the measurements to a
key=value file; a Python script converts it to JSON and checks it against
`spec.json`.

Initial `spec.json` limits:

| Measurement | Limit |
|---|---|
| Switching threshold VM (DC, Vout = Vin) | 0.70 V to 1.10 V |
| Output high VOH | at least 1.75 V |
| Output low VOL | at most 0.05 V |
| Propagation delay high-to-low tpHL (50% to 50%) | at most 100 ps |
| Propagation delay low-to-high tpLH (50% to 50%) | at most 100 ps |

These are the limits for the first milestone. Once committed, they change
only with the user's approval (section 7).

## 6. Reports and gates

### 6.1 Report format

Every stage writes one JSON file:

```json
{
  "stage": "drc",
  "design": "inv",
  "status": "pass",
  "metrics": {"magic_violations": 0, "klayout_violations": 0},
  "errors": [],
  "log": "build/inv/logs/drc_magic.log"
}
```

- `status` is `pass`, `fail` (the design does not meet the criteria) or
  `error` (the tool crashed, an input is missing, or the expected success
  evidence was not found).
- `errors` lists one item per problem. For DRC each item has the rule name,
  a short description and the location in microns, so it can be found and
  fixed in the layout script.
- `summary.json` lists each stage's status and points at its report.

### 6.2 Positive evidence

A stage passes only when the tool exited with code 0 **and** the parser
found the output that proves success. Examples:

- DRC: the Magic and KLayout outputs each contain an explicit violation
  count, and the layout they checked is not empty (the cell's bounding box
  is non-zero).
- LVS: Netgen's final result line reports a unique match.
- Sim: every measurement named in `spec.json` has a numeric value.

If the evidence is missing, the status is `error`, never `pass`. This guards
against false passes, for example a Magic run that failed to load the PDK
and reports zero DRC errors on an empty cell.

### 6.3 Self-test

`./flow selftest` builds each fixture in its own directory under
`build/selftest/` and checks the gates against them:

| Fixture | Expected result |
|---|---|
| `inv_drc_bad.tcl` (deliberate spacing violation) | `drc` status `fail`, at least 1 violation reported |
| `inv_lvs_bad.tcl` (missing connection, otherwise DRC-clean) | `drc` status `pass`, `lvs` status `fail` |

`selftest` exits 0 only if both fixtures behave as expected. It shows that
the DRC and LVS gates catch real problems.

## 7. Flow guide rules

`docs/FLOW.md` contains:

- How to run: `./flow up`, `./flow <target>`, `./flow down`; image tag;
  PDK paths inside the container; repo mount path.
- Flow order: netlist, sim, layout, drc, lvs, pex. Do not work on a later
  stage while an earlier one is failing.
- Fixing loop: read `build/<design>/reports/*.json` first and raw logs only
  when the report is not enough. Fix the source files (schematic, testbench,
  layout script), never files in `build/`.
- Never do the following without the user's explicit approval:
  - loosen `spec.json` limits or any pass criteria to make a gate pass
  - waive, skip or disable a DRC rule
  - edit PDK files
  - hand-edit GDS or any other generated output
  - change the pinned image tag
- Tapeout-related steps (shuttle prechecks, submission) need a review
  and sign-off, and are never treated as done without one.

## 8. Error handling

- Container not running: `flow` starts it. If the start fails (Docker
  Desktop not running), `flow` prints that and exits non-zero.
- Tool crash or missing input: the stage writes a report with status
  `error` and the log path, and exits non-zero.
- PDK files not found at the recorded paths: stage status `error` with the
  missing path in `errors`.

## 9. Out of scope for this milestone

- Process corners other than TT; temperature sweeps.
- Resistive parasitics (capacitance only for PEX).
- Digital flow (OpenLane 2). This is the next spec.
- Ring oscillator or other larger analog blocks.
- Tapeout prechecks, seal ring, density fill, antenna checks.
- A custom MCP server around the flow.
- GUI work: the noVNC desktop in the image can be enabled later for viewing,
  but no stage depends on it.
