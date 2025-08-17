# Ionizer Current Control (Safety-Critical)
# - PyQt5 GUI to read/set the ionizer current via OPC UA with ramping and hard limits
# - Safety rails: absolute max current, max ramp rate, emergency stop to 0 A
# - Failsafe: persist last applied current to a file; optionally restore on startup
# - Status indicator (gray/yellow/green/red) reflects idle/ramping/ok/stopped states
# - 1) Operator selects target and ramp rate; 2) timed ramp adjusts setpoint in steps
# - All OPC UA reads/writes happen through fixed nodes; UI shows current/target

import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QLabel, QPushButton, QDoubleSpinBox, QGroupBox, 
                            QFormLayout, QMessageBox, QFileDialog)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPalette
from opcua import Client, ua


class IonizerCurrentControl(QMainWindow):
    # Main window: builds UI, manages OPC UA session, handles ramping and failsafe I/O.
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ionizer Current Control - Safety Critical")
        self.setGeometry(100, 100, 500, 450)
        
        # --- Safety parameters (authoritative limits) ---
        self.MAX_CURRENT = 23.0   # absolute ceiling (A)
        self.MAX_RAMP_RATE = 1.0  # max slope (A/min) 
        
        # --- Control state ---
        self.current_value = 0.0      # last applied setpoint (A)
        self.target_current = 0.0     # operator target (A)
        self.ramp_active = False
        self.failsafe_file = os.path.join(os.path.expanduser("~"), "ionizer_current_failsafe.txt")
        
        # Build UI, connect OPC, load failsafe (with confirmation), then render labels.
        self.init_ui()
        self.init_opc()
        self.load_failsafe_with_confirmation()
        self.update_display()

    def init_ui(self):
        # Compose UI: file ops, live readbacks, ramp controls, status banner.
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # --- File Operations (backup/restore of last safe current) ---
        file_group = QGroupBox("File Operations")
        file_layout = QVBoxLayout()
        
        self.file_status = QLabel("No backup loaded")
        self.save_button = QPushButton("Save Current")
        self.save_button.clicked.connect(self.save_to_file)
        self.load_button = QPushButton("Load Backup")
        self.load_button.clicked.connect(self.load_from_file)
        
        file_layout.addWidget(self.file_status)
        file_layout.addWidget(self.save_button)
        file_layout.addWidget(self.load_button)
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # --- Readbacks and status indicator ---
        display_group = QGroupBox("Current Status")
        display_layout = QFormLayout()
        
        self.current_label = QLabel("--")  # actual from OPC (A)
        self.target_label = QLabel("--")   # operator target (A)
        self.status_indicator = QLabel()   # gray/yellow/green/red lamp
        self.status_indicator.setFixedSize(20, 20)
        self.set_indicator_color(self.status_indicator, Qt.gray)
        
        display_layout.addRow("Actual Current [A]:", self.current_label)
        display_layout.addRow("Target Current [A]:", self.target_label)
        display_layout.addRow("Status:", self.status_indicator)
        
        display_group.setLayout(display_layout)
        layout.addWidget(display_group)

        # --- Ramp controls (target + slope + start/stop + E-Stop) ---
        control_group = QGroupBox("Current Control")
        control_layout = QFormLayout()
        
        self.ramp_rate_input = QDoubleSpinBox()
        self.ramp_rate_input.setRange(0.01, self.MAX_RAMP_RATE)
        self.ramp_rate_input.setValue(0.1)
        self.ramp_rate_input.setDecimals(2)
        
        self.target_input = QDoubleSpinBox()
        self.target_input.setRange(0, self.MAX_CURRENT)
        self.target_input.setValue(0.0)
        self.target_input.setDecimals(2)
        
        self.ramp_button = QPushButton("Start Ramp")
        self.ramp_button.clicked.connect(self.toggle_ramp)
        
        self.stop_button = QPushButton("Emergency Stop")
        self.stop_button.setStyleSheet("background-color: red; color: white;")
        self.stop_button.clicked.connect(self.emergency_stop)
        
        control_layout.addRow(f"Ramp Rate [A/min] (Max {self.MAX_RAMP_RATE}):", self.ramp_rate_input)
        control_layout.addRow(f"Target Current [A] (Max {self.MAX_CURRENT}):", self.target_input)
        control_layout.addRow(self.ramp_button)
        control_layout.addRow(self.stop_button)
        
        control_group.setLayout(control_layout)
        layout.addWidget(control_group)

        # --- Status banner ---
        self.status_label = QLabel("Status: Ready")
        layout.addWidget(self.status_label)

    def set_indicator_color(self, label, color):
        # Small colored square to reflect state (idle/ramping/ok/stopped).
        palette = label.palette()
        palette.setColor(QPalette.Window, color)
        label.setAutoFillBackground(True)
        label.setPalette(palette)
        label.update()

    def init_opc(self):
        # Establish OPC UA session (single endpoint) and update banner.
        try:
            self.client = Client("opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2")
            self.client.connect()
            self.status_label.setText("Status: Connected to OPC server")
        except Exception as e:
            self.status_label.setText(f"Status: Connection failed - {str(e)}")
            QMessageBox.critical(self, "Error", f"OPC UA connection failed: {str(e)}")

    def load_failsafe_with_confirmation(self):
        # On startup: if a prior setpoint file exists, optionally restore and apply it.
        if not os.path.exists(self.failsafe_file):
            self.create_default_failsafe()
            return
            
        try:
            with open(self.failsafe_file, 'r') as f:
                value = float(f.read().strip())
                
                if not 0 <= value <= self.MAX_CURRENT:
                    raise ValueError("Value out of range")
                
                # Confirm with operator before applying.
                reply = QMessageBox.question(
                    self, 'Confirm Load',
                    f"Load previously saved current value: {value:.3f}A?",
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    self.current_value = value
                    if self.set_current(value):
                        self.file_status.setText(f"Loaded: {value:.3f}A")
                        self.status_label.setText(f"Loaded failsafe value: {value:.3f}A")
                    else:
                        self.file_status.setText("Load failed")
                else:
                    self.file_status.setText("Load cancelled")
                    self.status_label.setText("Using default 0A - load cancelled")
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Could not load failsafe: {str(e)}")
            self.create_default_failsafe()

    def create_default_failsafe(self):
        # Create a baseline failsafe file with 0 A, and set UI accordingly.
        self.current_value = 0.0
        with open(self.failsafe_file, 'w') as f:
            f.write("0.0")
        self.file_status.setText("Created new failsafe (0A)")
        self.status_label.setText("Created new failsafe file with 0A")

    def save_to_file(self):
        # Manual save of the current setpoint to an operator-chosen path.
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Current Value", 
            self.failsafe_file, 
            "Text Files (*.txt);;All Files (*)", 
            options=options
        )
        
        if file_path:
            try:
                with open(file_path, 'w') as f:
                    f.write(f"{self.current_value:.3f}")
                
                # Track new path as the active failsafe file.
                if file_path != self.failsafe_file:
                    self.failsafe_file = file_path
                
                self.file_status.setText(f"Saved: {self.current_value:.3f}A")
                self.status_label.setText(f"Current value saved to {file_path}")
                
                QMessageBox.information(self, "Success", f"Current value {self.current_value:.3f}A saved successfully")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file: {str(e)}")

    def load_from_file(self):
        # Manual load of a setpoint file; verify range; require confirmation; then apply.
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Backup File", 
            os.path.dirname(self.failsafe_file) if self.failsafe_file else "",
            "Text Files (*.txt);;All Files (*)", 
            options=options
        )
        
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    value = float(f.read().strip())
                
                # Hard range check before applying.
                if not 0 <= value <= self.MAX_CURRENT:
                    raise ValueError(f"Value {value}A out of range (0-{self.MAX_CURRENT}A)")
                
                # Confirm intent and provenance.
                reply = QMessageBox.question(
                    self, 'Confirm Load',
                    f"Load current value {value:.3f}A from file?\n\n"
                    f"File: {file_path}",
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    if self.set_current(value):
                        self.failsafe_file = file_path
                        self.file_status.setText(f"Loaded: {value:.3f}A")
                        self.status_label.setText(f"Loaded value from {file_path}")
                        QMessageBox.information(self, "Success", f"Current set to {value:.3f}A")
                    else:
                        QMessageBox.warning(self, "Error", "Could not set current value")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not load file: {str(e)}")

    def get_current(self):
        # Read actual ionizer current (A) from OPC UA (analog input).
        try:
            node = self.client.get_node("ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Ionisierer")
            value = node.get_value()
            return float(str(value)) if value is not None else None
        except Exception as e:
            self.status_label.setText(f"Read error: {str(e)}")
            return None

    def set_current(self, value):
        # Write ionizer current setpoint (A) to OPC UA (analog output) with range guard.
        if not 0 <= value <= self.MAX_CURRENT:
            self.status_label.setText(f"Error: Current {value}A out of range!")
            return False
            
        try:
            node = self.client.get_node("ns=3;s=OPC_1.PLC_HV/Analog_Out/Out_Cal_Ionisierer")
            variant = ua.Variant(float(value), ua.VariantType.Float)
            node.set_value(variant)
            self.current_value = value
            self.save_failsafe_automatic()
            return True
        except Exception as e:
            self.status_label.setText(f"Write error: {str(e)}")
            return False

    def save_failsafe_automatic(self):
        # Persist last applied current silently (used after successful writes and on exit).
        try:
            with open(self.failsafe_file, 'w') as f:
                f.write(f"{self.current_value:.3f}")
        except Exception as e:
            self.status_label.setText(f"Autosave failed: {str(e)}")

    def toggle_ramp(self):
        # Single button toggles ramping state.
        if self.ramp_active:
            self.stop_ramp()
        else:
            self.start_ramp()

    def start_ramp(self):
        # Configure and begin a timed ramp from current reading to target at given slope.
        self.target_current = self.target_input.value()
        ramp_rate = self.ramp_rate_input.value()
        
        # Range guard on target.
        if not 0 <= self.target_current <= self.MAX_CURRENT:
            QMessageBox.warning(self, "Error", f"Target current must be between 0 and {self.MAX_CURRENT}A")
            return
            
        # Use actual measured current as start point.
        current = self.get_current()
        if current is None:
            QMessageBox.critical(self, "Error", "Could not read current value")
            return
            
        delta = self.target_current - current
        if delta == 0:
            QMessageBox.information(self, "Info", "Already at target current")
            return
            
        direction = 1 if delta > 0 else -1
        # Convert slope (A/min) into total ramp time (ms).
        ramp_time = abs(delta) / ramp_rate * 60 * 1000
        
        self.ramp_active = True
        self.ramp_button.setText("Stop Ramp")
        self.set_indicator_color(self.status_indicator, Qt.yellow)
        self.status_label.setText(f"Ramping {'up' if direction > 0 else 'down'} to {self.target_current}A")
        
        # Discretize into 100 ms steps.
        self.ramp_steps = int(ramp_time / 100)
        self.ramp_step_size = delta / self.ramp_steps
        
        self.ramp_timer = QTimer(self)
        self.ramp_timer.timeout.connect(self.update_ramp)
        self.ramp_timer.start(100)

    def update_ramp(self):
        # Apply one ramp step; stop when target reached or write fails.
        if not self.ramp_active:
            return
            
        new_current = self.current_value + self.ramp_step_size
        
        # Snap to target when crossing it.
        if ((self.ramp_step_size > 0 and new_current >= self.target_current) or 
            (self.ramp_step_size < 0 and new_current <= self.target_current)):
            new_current = self.target_current
            self.ramp_complete()
        
        # Write step; abort ramp if write fails.
        if self.set_current(new_current):
            self.update_display()
        else:
            self.stop_ramp()

    def ramp_complete(self):
        # Successful ramp end: finalize UI state and inform operator.
        self.ramp_active = False
        self.ramp_timer.stop()
        self.ramp_button.setText("Start Ramp")
        self.set_indicator_color(self.status_indicator, Qt.green)
        self.status_label.setText(f"Ramp complete! Current at {self.target_current}A")
        QMessageBox.information(self, "Complete", "Current ramp completed successfully")

    def stop_ramp(self):
        # Gracefully stop an in-progress ramp (operator action or failure path).
        if self.ramp_active:
            self.ramp_active = False
            self.ramp_timer.stop()
            self.ramp_button.setText("Start Ramp")
            self.set_indicator_color(self.status_indicator, Qt.red)
            self.status_label.setText("Ramp stopped by user")

    def emergency_stop(self):
        # Hard stop: immediately set current to 0 A (with confirmation).
        if QMessageBox.question(self, "Confirm", "EMERGENCY STOP - Set current to 0A?", 
                              QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            self.stop_ramp()
            if self.set_current(0.0):
                self.update_display()
                self.status_label.setText("EMERGENCY STOP - Current set to 0A")
                self.set_indicator_color(self.status_indicator, Qt.red)

    def update_display(self):
        # Refresh labels from live readback and show the active target.
        current = self.get_current()
        if current is not None:
            self.current_label.setText(f"{current:.3f}")
            self.target_label.setText(f"{self.target_current:.3f}")

    def closeEvent(self, event):
        # On exit: warn if ramping, persist last value, and disconnect OPC cleanly.
        if self.ramp_active:
            if QMessageBox.question(self, "Ramp Active", 
                                  "A ramp is in progress. Really quit?",
                                  QMessageBox.Yes|QMessageBox.No) == QMessageBox.No:
                event.ignore()
                return
        
        # Save current state on exit (failsafe).
        self.save_failsafe_automatic()
        
        if hasattr(self, 'client') and self.client:
            self.client.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = IonizerCurrentControl()
    window.show()

    sys.exit(app.exec_())
