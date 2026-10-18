# ALIS-Control-Software

The first generation of control programs for the ALIS setup at the laser-assisted AMS beamline at
CologneAMS. Each script is a standalone PyQt5 program for one device or task. On the lab PCs they run as
executables built with PyInstaller.

These programs have been replaced by
[FLAVIA](https://github.com/Paranoidgrinch/FLAVIA-alpha-1.1-Fully-integrated-Laser-Anion-Variables-for-AMS-),
which combines them in one application. They remain here as a reference for the original device
communication.

## Programs

| Script | Purpose | Interface |
| --- | --- | --- |
| `ALIS_MENU_FINAL2.py` | OPC UA control panel for the digital and analog PLC channels | OPC UA |
| `Magnet_Final4.py` | Delta SM 60-100 magnet controller with current calculator (latest version) | TCP |
| `Magnet_Final3.py` | earlier version of the magnet controller | TCP |
| `Magnet_mit_Gaussmeter.py` | magnet controller with Hall-probe readback (EX-6030 / LakeShore 421) | TCP |
| `Keithley9.py` | Keithley 6485 picoammeter monitor with live plot (latest version) | TCP, OPC UA |
| `Keithley8.py` | earlier version of the Keithley monitor | TCP, OPC UA |
| `Ionizer_Ramp2.py` | ionizer current control with ramps | OPC UA |
| `Source_Maintainance_Commented.py` | ion-source maintenance control | OPC UA |
| `Sample_BLK_commented.py` | stepper-motor control of the sample wheel | TCP |
| `abgespeckt+bestätigung.py` | Verdi V18 laser controller, reduced version with confirmations | TCP |
| `laser+powermeter+statistics.py` | Verdi V18 controller with power-meter readout and statistics | TCP, VISA |
| `efi_clac.py` | efficiency calculator | none |

## Usage

Python 3.12 or newer.

```bash
python -m venv .venv
.venv/bin/pip install PyQt5 numpy matplotlib opcua pyvisa
.venv/bin/python Magnet_Final4.py
```

Device addresses are set at the top of each script. To build an executable:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed Magnet_Final4.py
```

## License

GPL-3.0, see [LICENSE](LICENSE).
