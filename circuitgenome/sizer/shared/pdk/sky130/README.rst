Minimal SKY130 PDK for CircuitGenome
------------------------------------

Trimmed from the SkyWater open PDK (``sky130A``, Apache-2.0, The SkyWater PDK
Authors): only the 1.8 V core MOSFETs across the five process corners.

**The files contain**

- ``ngspice/sky130.lib.spice`` --> Hand-written corner library
  (``tt``/``ss``/``ff``/``sf``/``fs``).  Each section mirrors the stock
  ``sky130.lib.spice`` corner sections: ``mc_mm_switch=0``/``mc_pr_switch=0``
  (Monte-Carlo off), ``.option scale=1.0u`` (from upstream ``all.spice`` —
  instance ``W``/``L`` are in **microns**), then the device model files.
- ``ngspice/lod.spice`` --> Corner-independent length-of-diffusion parameters
  (upstream ``libs.tech/ngspice/parameters/lod.spice``, unmodified).
- ``ngspice/sky130_fd_pr__{n,p}fet_01v8__*.spice`` --> Per-corner BSIM4
  model cards + mismatch parameter cards for ``sky130_fd_pr__nfet_01v8`` /
  ``sky130_fd_pr__pfet_01v8`` (upstream ``libs.ref/sky130_fd_pr/spice/``,
  unmodified).

**Device interface**

Devices are subcircuits ``X<name> d g s b sky130_fd_pr__{n,p}fet_01v8 w=<um>
l=<um>`` with the BSIM4 transistor at the internal instance
``msky130_fd_pr__{n,p}fet_01v8`` (operating-point handle
``@m.x<name>.msky130_fd_pr__nfet_01v8[...]``).

**Trim verification**

A 10-deck sweep (5 corners x both polarities, ``W/L = 10u/0.15u``,
``|Vgs| = 1.8 V``, ``|Vds| = 0.9 V``) reads ``id``/``gm``/``vth`` through this
trimmed library and through the full stock ``sky130A`` tree: all values agree
digit-for-digit (ngspice-46).
