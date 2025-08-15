import socket
import numpy as np
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import Qt
import sys
from datetime import datetime
import os
import glob
import time

class ScrollableSlider(QtWidgets.QSlider):
    def __init__(self, parent=None, step_func=None):
        super().__init__(Qt.Horizontal, parent)
        self.step_func = step_func

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        step = self.step_func() if self.step_func else 1
        if delta > 0:
            self.setValue(min(self.maximum(), self.value() + step))
        else:
            self.setValue(max(self.minimum(), self.value() - step))
        event.accept()

class DeltaMagnetController(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Delta SM 60-100 Magnet Controller and Calculator")
        self.setFixedSize(900, 950)  # Increased size for new controls
        
        # TCP Configuration (Magnet)
        self.HOST = "192.168.0.5"
        self.PORT = 8462
        self.sock = None

        # TCP Configuration (Gaussmeter über EX-6030)
        self.GM_HOST = "192.168.0.13"   # <<--- anpassen, falls nötig
        self.GM_PORT = 100
        self.gm_sock = None
        self._gm_write_delay = 0.06     # ~50–80 ms
        self._gm_read_idle = 0.25       # pro recv()-Wartezeit
        self._gm_read_overall = 0.8     # Gesamtlese-Timeout kurz halten
        self.last_field_kG = float("nan")

        # Initialize calculation variables
        self.mass = 1.0  # u
        self.extraction_voltage = 1000.0  # V
        self.sputter_voltage = 1000.0  # V
        
        # Current Switching State
        self.current_mode = "Manual"  # Manual | Auto
        self.current_values = [0.0, 0.0, 0.0]  # [Current1, Current2, Current3]
        self.data_counts = [10, 10, 10]  # Data counts [Count1, Count2, Count3]
        self.switch_timer = QtCore.QTimer()
        self.active_current_index = 0
        self.last_switch_time = datetime.now()
        self.last_data_count = 0
        self.data_counter = 0
        self.data_path = r"\\192.168.0.1\current analysis\*.blk"
        
        # Slider control variables
        self.multiplier = 1000  # For 3 decimal places precision
        self.current_step = 100  # Default step size (0.1 A)
        self.allowed_steps = [0.001, 0.01, 0.1, 1.0, 10.0]  # Allowed step sizes in A
        
        # Logging State
        self.logging_active = False
        self.log_file = None
        self.log_start_time = None
        
        # Mass Scan State
        self.scan_active = False
        self.scan_timer = QtCore.QTimer()
        self.scan_current = 0.0
        self.scan_start = 0.0
        self.scan_stop = 0.0
        self.scan_increment = 0.1
        
        # UI Setup
        self.create_ui()
        self.connect_device()
        self.connect_gaussmeter()  # <<-- Gaussmeter TCP verbinden
    
    # ---------------- Gaussmeter-Helfer ----------------
    def connect_gaussmeter(self):
        """Stellt TCP-Verbindung zum EX-6030 her und setzt das 421 (unverbindlich) auf Gauss/DC/Autorange."""
        try:
            if self.gm_sock:
                try:
                    self.gm_sock.close()
                except:
                    pass
                self.gm_sock = None
            s = socket.create_connection((self.GM_HOST, self.GM_PORT), timeout=2)
            s.settimeout(1.0)
            # kleine Grundkonfiguration; Antwort wird verworfen
            for cmd in (b"UNIT G", b"ACDC 0", b"AUTO 1"):
                s.sendall(cmd + b"\r\n")
                time.sleep(self._gm_write_delay)
                self._gm_read_line(s)  # lese ggf. eine Zeile
            self.gm_sock = s
        except Exception:
            self.gm_sock = None  # wir versuchen beim nächsten Tick erneut

    def _gm_read_line(self, sock):
        """Liest bis CR oder LF oder Timeout (ASCII)."""
        end = time.time() + self._gm_read_overall
        buf = bytearray()
        while time.time() < end:
            sock.settimeout(self._gm_read_idle)
            try:
                chunk = sock.recv(256)
            except TimeoutError:
                if buf:
                    break
                continue
            except OSError:
                return ""
            if not chunk:
                break
            buf += chunk
            if b"\r" in chunk or b"\n" in chunk:
                break
        try:
            return bytes(buf).strip().decode("ascii", "replace")
        except Exception:
            return ""

    def _gm_txrx(self, cmd: str):
        """Sende ASCII-Command (CRLF) und lies eine Antwortzeile."""
        if not self.gm_sock:
            return ""
        try:
            self.gm_sock.sendall(cmd.encode("ascii") + b"\r\n")
            time.sleep(self._gm_write_delay)
            return self._gm_read_line(self.gm_sock)
        except OSError:
            return ""

    def _gm_read_field_kG(self):
        """Liest das Feld und gibt es als kG (float) zurück. Rechnet korrekt, egal ob das 421 gerade T oder G sendet."""
        if not self.gm_sock:
            return float("nan")
        mult_map = {"µ": 1e-6, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3}
        val  = self._gm_txrx("FIELD?")
        mul  = self._gm_txrx("FIELDM?")
        unit = self._gm_txrx("UNIT?")
        try:
            base = float((val or "").strip())
        except Exception:
            return float("nan")
        value = base * mult_map.get((mul or "").strip(), 1.0)
        # jetzt in Gauss bringen
        if (unit or "").strip().upper().startswith("T"):
            value *= 1e4  # 1 T = 10,000 G
        # value ist in G -> nach kG
        return value / 1000.0

    # ---------------- UI ----------------
    def create_ui(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        # ====== Calculation Panel ======
        calc_group = QtWidgets.QGroupBox("Ion Beam Parameters")
        calc_layout = QtWidgets.QGridLayout()
        
        # Input Fields
        self.mass_input = QtWidgets.QDoubleSpinBox()
        self.mass_input.setRange(1, 500)
        self.mass_input.setValue(1.0)
        self.mass_input.setSuffix(" u")
        self.mass_input.valueChanged.connect(self.update_calculations)
        
        self.extraction_input = QtWidgets.QDoubleSpinBox()
        self.extraction_input.setRange(0, 100000)
        self.extraction_input.setValue(1000)
        self.extraction_input.setSuffix(" V")
        self.extraction_input.valueChanged.connect(self.update_calculations)
        
        self.sputter_input = QtWidgets.QDoubleSpinBox()
        self.sputter_input.setRange(0, 100000)
        self.sputter_input.setValue(1000)
        self.sputter_input.setSuffix(" V")
        self.sputter_input.valueChanged.connect(self.update_calculations)
        
        # Result Indicators
        self.b_field_indicator = QtWidgets.QLabel("0.00 kG")
        self.b_field_indicator.setStyleSheet("font-weight: bold; color: #2E86C1;")
        
        self.calc_current_indicator = QtWidgets.QLabel("0.00 A")
        self.calc_current_indicator.setStyleSheet("font-weight: bold; color: #27AE60;")
        
        # Set button
        self.set_calc_btn = QtWidgets.QPushButton("Set")
        self.set_calc_btn.clicked.connect(self.set_calculated_current)
        
        # Layout
        calc_layout.addWidget(QtWidgets.QLabel("Mass:"), 0, 0)
        calc_layout.addWidget(self.mass_input, 0, 1)
        calc_layout.addWidget(QtWidgets.QLabel("Extraction Voltage:"), 1, 0)
        calc_layout.addWidget(self.extraction_input, 1, 1)
        calc_layout.addWidget(QtWidgets.QLabel("Sputter Voltage:"), 2, 0)
        calc_layout.addWidget(self.sputter_input, 2, 1)
        calc_layout.addWidget(QtWidgets.QLabel("Calculated B Field:"), 0, 2)
        calc_layout.addWidget(self.b_field_indicator, 0, 3)
        calc_layout.addWidget(QtWidgets.QLabel("Required Current:"), 1, 2)
        calc_layout.addWidget(self.calc_current_indicator, 1, 3)
        calc_layout.addWidget(self.set_calc_btn, 2, 2, 1, 2)
        calc_group.setLayout(calc_layout)
        
        # Connection Status
        self.connection_label = QtWidgets.QLabel("Status: Disconnected")
        self.connection_label.setStyleSheet("font-size: 14px; color: red;")
        
        # ===== Manual Control Group =====
        manual_group = QtWidgets.QGroupBox("Manual Control")
        manual_layout = QtWidgets.QGridLayout()
        
        # Current Control - Slider Implementation
        self.decrease_btn = QtWidgets.QPushButton("◀")
        self.decrease_btn.clicked.connect(self.decrease_slider)
        
        self.current_slider = ScrollableSlider(step_func=lambda: self.current_step)
        self.current_slider.setMinimum(0)
        self.current_slider.setMaximum(120 * self.multiplier)
        self.current_slider.setValue(0)
        self.current_slider.valueChanged.connect(self.update_slider_label)
        self.current_slider.valueChanged.connect(self.update_slider_color)
        self.current_slider.valueChanged.connect(self.send_current_value)
        
        self.increase_btn = QtWidgets.QPushButton("▶")
        self.increase_btn.clicked.connect(self.increase_slider)
        
        # Step size selector
        self.step_selector = QtWidgets.QComboBox()
        for step in self.allowed_steps:
            self.step_selector.addItem(f"{step:.3f}")
        self.step_selector.setCurrentIndex(2)  # Default to 0.1
        self.step_selector.currentIndexChanged.connect(self.change_step_size)
        
        # Current value display
        self.current_value_label = QtWidgets.QLabel("0.000 A")
        self.current_value_label.setStyleSheet("font-weight: bold;")

        # NEW: Direct current input with send button
        self.direct_current_input = QtWidgets.QDoubleSpinBox()
        self.direct_current_input.setRange(0, 120.0)
        self.direct_current_input.setDecimals(4)
        self.direct_current_input.setSuffix(" A")
        self.direct_current_input.setValue(0.0)
        
        self.send_current_btn = QtWidgets.QPushButton("Send")
        self.send_current_btn.clicked.connect(self.send_direct_current)
        
        # Voltage Limit
        self.volt_spin = QtWidgets.QDoubleSpinBox()
        self.volt_spin.setRange(0, 100.0)
        self.volt_spin.setValue(60.0)
        self.volt_spin.setDecimals(3)
        self.volt_spin.setSuffix(" V")
        self.volt_spin.valueChanged.connect(self.send_current_value)
        
        # (zweite Definition war doppelt; wir lassen eine)
        
        # Layout for manual control
        manual_layout.addWidget(QtWidgets.QLabel("Target Current:"), 0, 0, 1, 2)
        manual_layout.addWidget(self.decrease_btn, 1, 0)
        manual_layout.addWidget(self.current_slider, 1, 1, 1, 3)
        manual_layout.addWidget(self.increase_btn, 1, 4)
        manual_layout.addWidget(QtWidgets.QLabel("Step Size (A):"), 2, 0)
        manual_layout.addWidget(self.step_selector, 2, 1)
        manual_layout.addWidget(self.current_value_label, 2, 3)
        manual_layout.addWidget(QtWidgets.QLabel("Direct Input:"), 3, 0)
        manual_layout.addWidget(self.direct_current_input, 3, 1)
        manual_layout.addWidget(self.send_current_btn, 3, 2)
        manual_layout.addWidget(QtWidgets.QLabel("Voltage Limit:"), 4, 0)
        manual_layout.addWidget(self.volt_spin, 4, 1)
        
        manual_group.setLayout(manual_layout)
        
        # ===== Automatic Switching Group =====
        auto_group = QtWidgets.QGroupBox("Current Switching")
        auto_layout = QtWidgets.QGridLayout()
        
        # Current 1
        self.current1_spin = QtWidgets.QDoubleSpinBox()
        self.current1_spin.setRange(0, 120.0)
        self.current1_spin.setDecimals(4)
        self.current1_spin.setSuffix(" A")
        
        self.count1_spin = QtWidgets.QSpinBox()
        self.count1_spin.setRange(1, 10000)
        self.count1_spin.setSuffix(" data points")
        
        # Current 2
        self.current2_spin = QtWidgets.QDoubleSpinBox()
        self.current2_spin.setRange(0, 120.0)
        self.current2_spin.setDecimals(4)
        self.current2_spin.setSuffix(" A")
        
        self.count2_spin = QtWidgets.QSpinBox()
        self.count2_spin.setRange(1, 10000)
        self.count2_spin.setSuffix(" data points")
        
        # Current 3 (optional)
        self.enable_current3 = QtWidgets.QCheckBox("Enable Current 3")
        self.enable_current3.stateChanged.connect(self.toggle_current3)
        
        self.current3_spin = QtWidgets.QDoubleSpinBox()
        self.current3_spin.setRange(0, 120.0)
        self.current3_spin.setDecimals(4)
        self.current3_spin.setSuffix(" A")
        self.current3_spin.setEnabled(False)
        
        self.count3_spin = QtWidgets.QSpinBox()
        self.count3_spin.setRange(1, 10000)
        self.count3_spin.setSuffix(" data points")
        self.count3_spin.setEnabled(False)
        
        # Mode Control
        self.auto_btn = QtWidgets.QPushButton("Start Auto Switching")
        self.auto_btn.clicked.connect(self.toggle_auto_mode)
        
        self.status_label = QtWidgets.QLabel("Mode: Manual")
        self.data_counter_label = QtWidgets.QLabel("Data changes detected: 0")
        
        # Path configuration
        self.path_label = QtWidgets.QLabel("Data Path:")
        self.path_edit = QtWidgets.QLineEdit(r"\\192.168.0.1\current analysis\*.blk")
        self.path_edit.textChanged.connect(self.update_data_path)
        
        auto_layout.addWidget(QtWidgets.QLabel("Current 1:"), 0, 0)
        auto_layout.addWidget(self.current1_spin, 0, 1)
        auto_layout.addWidget(QtWidgets.QLabel("Data Count:"), 0, 2)
        auto_layout.addWidget(self.count1_spin, 0, 3)
        
        auto_layout.addWidget(QtWidgets.QLabel("Current 2:"), 1, 0)
        auto_layout.addWidget(self.current2_spin, 1, 1)
        auto_layout.addWidget(QtWidgets.QLabel("Data Count:"), 1, 2)
        auto_layout.addWidget(self.count2_spin, 1, 3)
        
        auto_layout.addWidget(self.enable_current3, 2, 0)
        auto_layout.addWidget(self.current3_spin, 2, 1)
        auto_layout.addWidget(QtWidgets.QLabel("Data Count:"), 2, 2)
        auto_layout.addWidget(self.count3_spin, 2, 3)
        
        auto_layout.addWidget(self.path_label, 3, 0)
        auto_layout.addWidget(self.path_edit, 3, 1, 1, 3)
        
        auto_layout.addWidget(self.auto_btn, 4, 0, 1, 2)
        auto_layout.addWidget(self.status_label, 4, 2, 1, 1)
        auto_layout.addWidget(self.data_counter_label, 4, 3, 1, 1)
        auto_group.setLayout(auto_layout)
        
        # ===== Mass Scan Group =====
        scan_group = QtWidgets.QGroupBox("Mass Scan")
        scan_layout = QtWidgets.QGridLayout()
        
        # Scan Parameters
        self.scan_start_input = QtWidgets.QDoubleSpinBox()
        self.scan_start_input.setRange(0, 120.0)
        self.scan_start_input.setDecimals(4)
        self.scan_start_input.setSuffix(" A")
        self.scan_start_input.setValue(0.0)
        
        self.scan_stop_input = QtWidgets.QDoubleSpinBox()
        self.scan_stop_input.setRange(0, 120.0)
        self.scan_stop_input.setDecimals(4)
        self.scan_stop_input.setSuffix(" A")
        self.scan_stop_input.setValue(10.0)
        
        self.scan_increment_input = QtWidgets.QDoubleSpinBox()
        self.scan_increment_input.setRange(0.001, 10.0)
        self.scan_increment_input.setDecimals(4)
        self.scan_increment_input.setSuffix(" A/s")
        self.scan_increment_input.setValue(0.1)
        
        # Scan Controls
        self.scan_enable_check = QtWidgets.QCheckBox("Enable Mass Scan")
        self.scan_start_btn = QtWidgets.QPushButton("Start Scan")
        self.scan_start_btn.clicked.connect(self.toggle_scan)
        self.scan_status_label = QtWidgets.QLabel("Status: Ready")
        
        # Progress Bar
        self.scan_progress = QtWidgets.QProgressBar()
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        
        # Layout
        scan_layout.addWidget(QtWidgets.QLabel("Start Value:"), 0, 0)
        scan_layout.addWidget(self.scan_start_input, 0, 1)
        scan_layout.addWidget(QtWidgets.QLabel("Stop Value:"), 1, 0)
        scan_layout.addWidget(self.scan_stop_input, 1, 1)
        scan_layout.addWidget(QtWidgets.QLabel("Increment:"), 2, 0)
        scan_layout.addWidget(self.scan_increment_input, 2, 1)
        scan_layout.addWidget(self.scan_enable_check, 3, 0)
        scan_layout.addWidget(self.scan_start_btn, 3, 1)
        scan_layout.addWidget(self.scan_status_label, 4, 0, 1, 2)
        scan_layout.addWidget(self.scan_progress, 5, 0, 1, 2)
        
        scan_group.setLayout(scan_layout)
        
        # ===== Measurements Group =====
        meas_group = QtWidgets.QGroupBox("Measurements")
        meas_layout = QtWidgets.QFormLayout()
        
        self.meas_current = QtWidgets.QLabel("--- A")
        self.meas_voltage = QtWidgets.QLabel("--- V")
        self.meas_field   = QtWidgets.QLabel("--- kG")   # <<-- NEU: Feldanzeige
        self.current_state_label = QtWidgets.QLabel("Current State: ---")
        
        meas_layout.addRow("Current:", self.meas_current)
        meas_layout.addRow("Voltage:", self.meas_voltage)
        meas_layout.addRow("Magnetic Field:", self.meas_field)  # <<-- NEU
        meas_layout.addRow("State:", self.current_state_label)
        meas_group.setLayout(meas_layout)
        
        # ===== Logging Controls =====
        log_group = QtWidgets.QGroupBox("Data Logging")
        log_layout = QtWidgets.QHBoxLayout()
        
        self.log_btn = QtWidgets.QPushButton("Start Logging")
        self.log_btn.clicked.connect(self.toggle_logging)
        self.log_status = QtWidgets.QLabel("Logging: Inactive")
        self.log_file_label = QtWidgets.QLabel("No file selected")
        
        log_layout.addWidget(self.log_btn)
        log_layout.addWidget(self.log_status)
        log_layout.addWidget(self.log_file_label)
        log_group.setLayout(log_layout)
        
        # ===== Assemble Main UI =====
        layout.addWidget(calc_group)
        layout.addWidget(self.connection_label)
        layout.addWidget(manual_group)
        layout.addWidget(auto_group)
        layout.addWidget(scan_group)
        layout.addWidget(meas_group)
        layout.addWidget(log_group)
        
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        
        # Timers
        self.measurement_timer = QtCore.QTimer()
        self.measurement_timer.timeout.connect(self.update_measurements)
        self.measurement_timer.start(1000)  # 1 second refresh
        
        self.switch_timer = QtCore.QTimer()
        self.switch_timer.timeout.connect(self.check_data_count)
        self.switch_timer.setInterval(5000)  # Check every 5 seconds
        
        self.scan_timer = QtCore.QTimer()
        self.scan_timer.timeout.connect(self.update_scan)
        self.scan_timer.setInterval(1000)  # 1 second interval for scan updates
        
        # Initialize slider color
        self.update_slider_color(0)

    # NEW: Method to handle direct current input
    def send_direct_current(self):
        """Set the current to the value specified in the direct input field"""
        current = self.direct_current_input.value()
        if 0 <= current <= 120.0:  # Validate range
            # Update slider to reflect the new current value
            self.current_slider.setValue(int(round(current * self.multiplier)))
            # The slider's valueChanged signal will trigger send_current_value()
        else:
            QtWidgets.QMessageBox.warning(self, "Invalid Value", 
                                        "Current must be between 0 and 120 A")

    # ===== Mass Scan Methods =====
    def toggle_scan(self):
        if not self.scan_active:
            # Start scan
            if not self.scan_enable_check.isChecked():
                QtWidgets.QMessageBox.warning(self, "Warning", "Please enable mass scan first")
                return
            
            self.scan_start = self.scan_start_input.value()
            self.scan_stop = self.scan_stop_input.value()
            self.scan_increment = self.scan_increment_input.value()
            
            if self.scan_start == self.scan_stop:
                QtWidgets.QMessageBox.warning(self, "Warning", "Start and stop values cannot be equal")
                return
            
            if (self.scan_increment > 0 and self.scan_start > self.scan_stop) or \
               (self.scan_increment < 0 and self.scan_start < self.scan_stop):
                QtWidgets.QMessageBox.warning(self, "Warning", "Increment direction doesn't match start/stop values")
                return
            
            self.scan_current = self.scan_start
            self.scan_active = True
            self.scan_start_btn.setText("Stop Scan")
            self.scan_status_label.setText("Status: Running")
            
            # Set initial current
            self.set_scan_current()
            
            # Start timer
            self.scan_timer.start()
        else:
            # Stop scan
            self.scan_active = False
            self.scan_timer.stop()
            self.scan_start_btn.setText("Start Scan")
            self.scan_status_label.setText("Status: Stopped")
            self.scan_progress.setValue(0)
    
    def update_scan(self):
        if not self.scan_active:
            return
        
        # Update current
        self.scan_current += self.scan_increment
        
        # Check if we've reached the target
        if (self.scan_increment > 0 and self.scan_current >= self.scan_stop) or \
           (self.scan_increment < 0 and self.scan_current <= self.scan_stop):
            self.scan_current = self.scan_stop
            self.scan_active = False
            self.scan_timer.stop()
            self.scan_start_btn.setText("Start Scan")
            self.scan_status_label.setText("Status: Complete")
        
        # Set the new current
        self.set_scan_current()
        
        # Update progress
        progress = int(100 * (self.scan_current - self.scan_start) / (self.scan_stop - self.scan_start))
        self.scan_progress.setValue(progress)
    
    def set_scan_current(self):
        """Set the current for the scan"""
        if self.sock:
            current = self.scan_current
            voltage = self.volt_spin.value()
            
            # Update slider to reflect current value
            self.current_slider.setValue(int(round(current * self.multiplier)))
            
            # Send commands to device
            self.send_command(f"sour:curr {current:.4f}")
            self.send_command(f"sour:volt {voltage:.3f}")
    
    # ===== Calculation Methods =====
    def set_calculated_current(self):
        """Set the slider to the calculated current value"""
        try:
            current_text = self.calc_current_indicator.text()
            current_value = float(current_text.split()[0])
            self.current_slider.setValue(int(round(current_value * self.multiplier)))
        except Exception as e:
            print(f"Error setting calculated current: {e}")

    # ===== Slider Control Methods =====
    def update_slider_label(self, value):
        real_value = value / self.multiplier
        formatted = f"{real_value:.3f} A"
        self.current_value_label.setText(formatted)
        
    def change_step_size(self, index):
        step_value = self.allowed_steps[index]
        self.current_step = int(step_value * self.multiplier)
        
    def decrease_slider(self):
        new_value = max(self.current_slider.minimum(), self.current_slider.value() - self.current_step)
        self.current_slider.setValue(new_value)
        
    def increase_slider(self):
        new_value = min(self.current_slider.maximum(), self.current_slider.value() + self.current_step)
        self.current_slider.setValue(new_value)
            
    def update_slider_color(self, value):
        hue = 120 - int((value / (120 * self.multiplier)) * 120)
        color = f"hsl({hue}, 100%, 50%)"

        self.current_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: #ddd;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: black;
                border: none;
                width: 15px;
                height: 15px;
                margin: -4px 0;
                border-radius: 6px;
            }}
            QSlider::sub-page:horizontal {{
                background: {color};
                border-radius: 2px;
            }}
            QSlider::add-page:horizontal {{
                background: #ddd;
                border-radius: 2px;
            }}
        """)
    
    def send_current_value(self):
        """Automatically send current value when slider changes"""
        if self.current_mode == "Manual" and self.sock and not self.scan_active:
            current = self.current_slider.value() / self.multiplier
            voltage = self.volt_spin.value()
            self.send_command(f"sour:curr {current:.4f}")
            self.send_command(f"sour:volt {voltage:.3f}")
    
    # ===== Rest of the methods remain unchanged =====
    def toggle_current3(self, state):
        """Enable/disable current 3 controls"""
        self.current3_spin.setEnabled(state == Qt.Checked)
        self.count3_spin.setEnabled(state == Qt.Checked)
    
    def update_data_path(self):
        """Update the data path when edited"""
        self.data_path = self.path_edit.text()
    
    def connect_device(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(2.0)
            self.sock.connect((self.HOST, self.PORT))
            self.connection_label.setText("Status: Connected")
            self.connection_label.setStyleSheet("color: green;")
            
            # Initialize with zero current/voltage
            self.send_command("sour:volt 0")
            self.send_command("sour:curr 0")
        except Exception as e:
            self.connection_label.setText(f"Status: Error ({str(e)})")
    
    def send_command(self, cmd):
        """Send command with line feed (\\n) and return response"""
        try:
            self.sock.sendall((cmd + "\n").encode('ascii'))
            if cmd.endswith("?"):
                return self.sock.recv(1024).decode('ascii').strip()
            return None
        except Exception as e:
            print(f"Command '{cmd}' failed: {e}")
            return None
    
    def toggle_auto_mode(self):
        if self.current_mode == "Manual":
            # Start auto mode
            self.current_mode = "Auto"
            
            # Get current values
            self.current_values = [
                self.current1_spin.value(),
                self.current2_spin.value(),
                self.current3_spin.value() if self.enable_current3.isChecked() else None
            ]
            
            # Get data counts
            self.data_counts = [
                self.count1_spin.value(),
                self.count2_spin.value(),
                self.count3_spin.value() if self.enable_current3.isChecked() else None
            ]
            
            # Remove None values if current3 is disabled
            if not self.enable_current3.isChecked():
                self.current_values = self.current_values[:2]
                self.data_counts = self.data_counts[:2]
            
            self.active_current_index = 0
            self.last_switch_time = datetime.now()
            self.data_counter = 0
            self.last_data_count = self.get_data_count()
            
            # Apply first current
            self.apply_auto_current()
            
            self.auto_btn.setText("Stop Auto Switching")
            self.status_label.setText(f"Mode: Auto (Current {self.active_current_index + 1})")
            self.current_state_label.setText(f"Current State: {self.active_current_index + 1}")
            self.switch_timer.start()  # Start checking data count
        else:
            # Stop auto mode
            self.current_mode = "Manual"
            self.switch_timer.stop()
            self.auto_btn.setText("Start Auto Switching")
            self.status_label.setText("Mode: Manual")
            self.current_state_label.setText("Current State: ---")
            
            # Zero out
            self.send_command("sour:volt 0")
            self.send_command("sour:curr 0")
    
    def get_data_count(self):
        """Get the current number of data files"""
        try:
            data = glob.glob(self.data_path)
            return len(data)
        except Exception as e:
            print(f"Error getting data count: {e}")
            return 0
    
    def check_data_count(self):
        """Check if data count has changed enough to switch currents"""
        current_count = self.get_data_count()
        if current_count > self.last_data_count:
            # Data count has increased
            change = current_count - self.last_data_count
            self.data_counter += change
            self.last_data_count = current_count
            self.data_counter_label.setText(f"Data changes: {self.data_counter}")
            
            # Check if we've reached the required count
            required_count = self.data_counts[self.active_current_index]
            if self.data_counter >= required_count:
                self.data_counter = 0
                self.next_current()
    
    def next_current(self):
        """Switch to the next current in sequence"""
        # Determine next index
        if self.enable_current3.isChecked():
            self.active_current_index = (self.active_current_index + 1) % 3
        else:
            self.active_current_index = (self.active_current_index + 1) % 2
        
        self.apply_auto_current()
        self.status_label.setText(f"Mode: Auto (Current {self.active_current_index + 1})")
        self.current_state_label.setText(f"Current State: {self.active_current_index + 1}")
    
    def apply_auto_current(self):
        """Apply the current active current setting"""
        current = self.current_values[self.active_current_index]
        voltage = self.volt_spin.value()  # Use manual voltage limit
        
        self.send_command(f"sour:curr {current:.4f}")
        self.send_command(f"sour:volt {voltage:.3f}")
        
        self.last_switch_time = datetime.now()
        self.data_counter_label.setText(f"Data changes: {self.data_counter}")

    def update_calculations(self):
        """Calculate magnetic field and required current"""
        try:
            # Get inputs
            mass_u = self.mass_input.value()
            extraction_v = self.extraction_input.value()
            sputter_v = self.sputter_input.value()
            
            # Convert to SI units
            mass_kg = mass_u * 1.66054e-27  # kg
            total_energy_ev = extraction_v + sputter_v  # eV
            
            # Corrected magnetic field calculation
            b_field_tesla = np.sqrt(2 * total_energy_ev * 1.60218e-19 * mass_kg) / (1.60218e-19 * 0.5)  
            b_field_kilogauss = b_field_tesla * 10  # 1 T = 10 kG
            
            # Current calculation (your formula)
            current = (b_field_kilogauss - 0.0937) / 0.1055
            
            # Update displays
            self.b_field_indicator.setText(f"{b_field_kilogauss:.2f} kG")
            self.calc_current_indicator.setText(f"{current:.4f} A")
        except Exception as e:
            print(f"Calculation error: {e}")

    def update_measurements(self):
        """Jede Sekunde: Strom/Spannung vom Netzteil + Feld (kG) vom Gaussmeter lesen, anzeigen & ggf. loggen."""
        # --- Magnet: Current & Voltage ---
        current_val = None
        voltage_val = None

        current = self.send_command("meas:curr?")
        voltage = self.send_command("meas:volt?")
        
        if current:
            try:
                current_val = float(current)
                self.meas_current.setText(f"{current_val:.4f} A")
            except ValueError:
                self.meas_current.setText("--- A")
        else:
            self.meas_current.setText("--- A")
        
        if voltage:
            try:
                voltage_val = float(voltage)
                self.meas_voltage.setText(f"{voltage_val:.3f} V")
            except ValueError:
                self.meas_voltage.setText("--- V")
        else:
            self.meas_voltage.setText("--- V")

        # --- Gaussmeter: Field (kG) ---
        # Reconnect versuchen, falls keine Verbindung besteht
        if self.gm_sock is None:
            self.connect_gaussmeter()

        field_kG = float("nan")
        if self.gm_sock:
            try:
                field_kG = self._gm_read_field_kG()
                if field_kG != field_kG:  # NaN => Fehler, reconnect nächster Tick
                    try:
                        self.gm_sock.close()
                    except:
                        pass
                    self.gm_sock = None
                else:
                    self.last_field_kG = field_kG
                    self.meas_field.setText(f"{field_kG:.3f} kG")
            except Exception:
                try:
                    if self.gm_sock:
                        self.gm_sock.close()
                except:
                    pass
                self.gm_sock = None
                self.meas_field.setText("--- kG")
        else:
            # keine Verbindung
            self.meas_field.setText("--- kG")

        # --- Logging (wenn aktiv) ---
        if self.logging_active and self.log_file:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            # nutze die zuletzt gültigen Werte; bei None/NaN schreibe leer
            curr_str = f"{current_val:.6f}" if isinstance(current_val, float) else ""
            volt_str = f"{voltage_val:.6f}" if isinstance(voltage_val, float) else ""
            field_str = f"{self.last_field_kG:.6f}" if self.last_field_kG == self.last_field_kG else ""
            self.log_file.write(f"{ts},{curr_str},{volt_str},{field_str}\n")
            self.log_file.flush()

    def toggle_logging(self):
        if not self.logging_active:
            # Start logging - prompt for file location
            options = QtWidgets.QFileDialog.Options()
            file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Select Log File", "", "Text Files (*.txt);;All Files (*)", options=options)
            
            if file_path:
                try:
                    # Open the file in append mode
                    self.log_file = open(file_path, 'a')
                    self.log_start_time = datetime.now()
                    
                    # Write header if file is empty
                    if os.stat(file_path).st_size == 0:
                        # Jetzt mit Spannung und Feld in kG
                        self.log_file.write("Timestamp,Current(A),Voltage(V),Field(kG)\n")
                    
                    self.logging_active = True
                    self.log_btn.setText("Stop Logging")
                    self.log_status.setText("Logging: Active")
                    self.log_file_label.setText(os.path.basename(file_path))
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "Error", f"Could not open log file: {str(e)}")
        else:
            # Stop logging
            self.logging_active = False
            if self.log_file:
                self.log_file.close()
                self.log_file = None
            
            self.log_btn.setText("Start Logging")
            self.log_status.setText("Logging: Inactive")
            self.log_file_label.setText("No file selected")
    
    def closeEvent(self, event):
        """Cleanup on exit with user confirmation"""
        # Stop any active processes first
        if self.current_mode == "Auto":
            self.toggle_auto_mode()  # Stop auto mode first
        
        if self.logging_active:
            self.toggle_logging()  # Stop logging
        
        if self.scan_active:
            self.toggle_scan()  # Stop scan
    
        # Ask user for confirmation
        reply = QtWidgets.QMessageBox.question(
            self, 'Confirm Exit',
            "Do you want to shut down the magnet (go to 0A)?\n"
            "Click 'No' to keep the current values.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes
        )
    
        if self.sock:
            if reply == QtWidgets.QMessageBox.Yes:
                # Shut down magnet
                self.send_command("sour:volt 0")
                self.send_command("sour:curr 0")
            else:
                # Keep current values - use proper string formatting
                self.send_command(r"sour:volt\s0\nsour:curr\s0\n")  # Using raw string
        
            # Close the socket connection
            self.sock.close()

        if self.gm_sock:
            try:
                self.gm_sock.close()
            except:
                pass
    
        event.accept()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = DeltaMagnetController()
    window.show()
    sys.exit(app.exec_())
