# ALIS Control Software

The first generation of control programs for the **ALIS** setup of the laser-assisted AMS beamline at
CologneAMS. Each script is a standalone PyQt5 tool for one device or task; on
the lab PCs they run as single executables built with PyInstaller.

> These tools have been superseded by
> [FLAVIA](https://github.com/Paranoidgrinch/FLAVIA-alpha-1.1-Fully-integrated-Laser-Anion-Variables-for-AMS-),
> which integrates all of them into one application with a common data model. They are kept here as the
> reference for the original device communication.

[![License](https://img.shields.io/badge/license-GPL--3.0-394B59?style=flat-square)](LICENSE)

## Tools

| Script | Tool | Communication |
| --- | --- | --- |
| `ALIS_MENU_FINAL2.py` | OPC UA control panel for the PLC-side digital and analog channels | OPC UA |
| `Magnet_Final4.py` | Delta SM 60-100 magnet controller with magnet-current calculator (latest) | TCP |
| `Magnet_Final3.py` | earlier version of the magnet controller | TCP |
| `Magnet_mit_Gaussmeter.py` | magnet controller with Hall-probe readback (EX-6030 / LakeShore 421) | TCP |
| `Keithley9.py` | Keithley 6485 picoammeter monitor with live plot (latest) | TCP, OPC UA |
| `Keithley8.py` | earlier version of the Keithley monitor | TCP, OPC UA |
| `Ionizer_Ramp2.py` | ionizer current control with safe ramps | OPC UA |
| `Source_Maintainance_Commented.py` | ion-source maintenance control | OPC UA |
| `Sample_BLK_commented.py` | stepper-motor control of the sample wheel | TCP |
| `abgespeckt+bestätigung.py` | Verdi V18 laser controller (slim version with confirmations) | TCP |
| `laser+powermeter+statistics.py` | Verdi V18 controller with power-meter readout and statistics | TCP, VISA |
| `efi_clac.py` | efficiency calculator | — |

## Running a tool

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install PyQt5 numpy matplotlib opcua pyvisa
.venv/bin/python Magnet_Final4.py
```

Device addresses are set at the top of each script and must match your network. To build an executable:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed Magnet_Final4.py
```

## License

GPL-3.0 — see [LICENSE](LICENSE).
