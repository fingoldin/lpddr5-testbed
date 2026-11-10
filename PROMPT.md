Design a PCB that is the same form factor as a DDR4 SODIMM to plug into a zcu104 Xilinx FPGA eval board to allow for a custom memory controller in LPDDR5.
We want to achieve the highest level of signal integrity for the command bus, NOT reads and writes. This is for a custom memory controller for processing-in-memory,
which is implemented with a high speed command stream, based on prior literature work like ComputeDRAM. So, reads and writes need to work at reasonable speeds,
but it's more important that the command bus is solid.

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

Start by:
- Explore references/. In particular, read references/hs_design, which has the Bible of electromagnetics.
- Explore how Antmicro does their tester board and how it integrates into their testbed in ./sodimm-ddr5-tester/.
- Answer carefully: Will the pseudo-differential strategy work for reads? For example, there may be lots of switching noise/crosstalk on the long DC N wires.

Use kicad 10.

Always assume that any gateware change on the host FPGA (zcu104) is trivial and can be done. It's always easier to do that than change the physical PCB design.

There is also a full suite of Ansys EDT tools available on a remote server. Explore it at 
`ssh -i ~/.ssh/id_macbook vassilios-kaxiras@compute-1.hardware.normalcomputing.net` and run `ansysedt` to get started.
You can use SiWave for large simulations, and HFSS for small scale more accurate 3D simulations (like of the DDR4 sodimm socket).

In the end, produce a design that's almost ready for fabrication. No need to target a specific fab vendor for now.
