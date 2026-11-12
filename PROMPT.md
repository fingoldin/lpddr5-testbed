Design a PCB that is the same form factor as a DDR4 SODIMM to plug into a zcu104 Xilinx FPGA eval board to allow for a custom memory controller in LPDDR5.
We want to achieve the highest level of signal integrity for the command bus, NOT reads and writes. This is for a custom memory controller for processing-in-memory,
which is implemented with a high speed command stream, based on prior literature work like ComputeDRAM. So, reads and writes need to work at reasonable speeds,
but it's more important that the command bus is solid.

Target the SK Hynix part H58G56BK7BX068N. Assume it has the same pinout as the JEDEC/equivalent Micron parts.

The high level idea is similar to the Antmicro LPDDR5 testbed, but with major differences. We want to have a single LPDDR5 chip soldered onto the DDR4 SODIMM board,
but it has to communicate differently. Writes and commands are sent with resisitive divider level shifters, while reads are directly wired to the zcu104
with a pseudo-differential scheme where N pins are wired to a DAC maintaining ~half the swing voltage.

Choose the assignment of banks to read, write, and command duty by minimizing the routing delay for the command bus.

Keep the same layer organization as the antmicro board. Make it as a minimal as possible overall. Keep the same routing conventions, the same stackup.
Do not overlap wires in high speed groups. Keep clean routing of high speed wires like in the antmicro design, with parallel clean fanout bundles
and 90-135 degree bends (no sharp corners).

Focus on only a single channel, a single byte, and a single rank. Choose which ones to maximize signal integrity and clean routing.

Be as minimal and simple as possible. For example, for the power supply design, make sure everything has enough current but there's no need to implement some really complicated
power-on chain or string together many ICs when a simpler, more dumb design will almost certainly work.

Remove any Antmicro or OpenHardware branding in the board files.

Use kicad 10.

Always assume that any gateware change on the host FPGA (zcu104) is trivial and can be done. It's always easier to do that than change the physical PCB design.

There is also a full suite of Ansys EDT tools available on a remote server. Explore it at 
`ssh -i ~/.ssh/id_macbook vassilios-kaxiras@compute-1.hardware.normalcomputing.net` and run `ansysedt` to get started.
You can use SiWave for large simulations, and HFSS for small scale more accurate 3D simulations (like of the DDR4 sodimm socket).
KiCad supports export in the IPC-2581 format, which can be imported directly into HFSS 3D Layout.

Vivado is available as version 2026. See ~/.bashrc.

In the end, produce a design that's almost ready for fabrication. No need to target a specific fab vendor for now.

I've already begun a bit with deleting parts of the Antmicro design.

You are only allowed to use git commands that do not modify the state - no adding, commiting, etc.

When you are placing and routing, periodically check the whole layer and the whole board to check that everything still looks good. It's easy to get too focused
on one aspect and forget to take a step back and realize you're going down a wrong path. Don't be afraid to rip up and restart. Be critical. 

When routing read/write byte groups, and the command bundles, delay match the wires within one group.

When routing high speed wires, follow Antmicro's convention: groups of high speed wires in a layer without much else, sandwiched by ground layers.
Always route differential pairs tightly together.

Keep the resistive dividers near the card fingers.

Start by:
- Explore references/. In particular, read references/hs_design, which has the Bible of electromagnetics.
- Explore how Antmicro does their tester board and how it integrates into their testbed in ./sodimm-ddr5-tester/.
- Answer carefully: Will the pseudo-differential strategy work for reads? For example, there may be lots of switching noise/crosstalk on the long DC N wires. Another issue
  is how to receive the data; one way is using MIPI_DPHY_DCI, but it's unclear if that will work well with pseudo-differential.
Spawn a few subagents to generate a few different ideas for each question above. Generate also a few different ideas for the initial design, being
the high level parts list and how commands, reads, and writes will work in a bit of detail. The present these findings to me before doing any changes to the board.

