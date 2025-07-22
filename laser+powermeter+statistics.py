import socket
import time
import statistics
from datetime import datetime
from collections import deque
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import QThread, pyqtSignal
import pyvisa  # For Thorlabs powermeter

class VerdiLaserController(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verdi V18 Laser Controller")
        self.setFixedSize(1000, 1050)  # Increased height for new controls
        
        # Default TCP Configuration
        self.DEFAULT_HOST = "192.168.0.7"
        self.DEFAULT_PORT = 103
        self.HOST = self.DEFAULT_HOST
        self.PORT = self.DEFAULT_PORT
        self.sock = None
        self.log_file = None
        self.logging_enabled = False
        self.running = False
        
        # Thorlabs powermeter
        self.powermeter = None
        self.powermeter_connected = False
        self.average_window_size = 10  # Default number of readings to average
        self.power_readings = deque(maxlen=self.average_window_size)  # Store recent readings
        
        # Laser state
        self.keyswitch_enabled = False
        self.system_ok = False
        self.laser_on = False
        self.shutter_on = False
        self.requested_power = 0.0  # Now using float
        
        # UI Setup
        self.create_ui()
        
        # Ask for logging
        self.prompt_logging()

    def update_powermeter_display(self, power):
        """Update the powermeter display with new reading"""
        self.powermeter_label.setText(f"{power:.6f} W")
        self.power_readings.append(power)  # Add to readings history
        self.update_average_display()

    def update_average_display(self):
        """Update the average power display and standard deviation"""
        if len(self.power_readings) > 0:
            avg_power = sum(self.power_readings) / len(self.power_readings)
            self.average_power_label.setText(f"{avg_power:.6f} W")
            
            # Calculate standard deviation
            if len(self.power_readings) > 1:
                sigma = statistics.stdev(self.power_readings)
                self.sigma_label.setText(f"±{sigma:.6f} W")
            else:
                self.sigma_label.setText("±- W")  # Not enough data
            
            self.readings_count_label.setText(f"{len(self.power_readings)}/{self.average_window_size} readings")

    def create_ui(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        # Connection Settings
        conn_settings_group = QtWidgets.QGroupBox("Connection Settings")
        conn_settings_layout = QtWidgets.QGridLayout()
        
        self.ip_input = QtWidgets.QLineEdit(self.DEFAULT_HOST)
        self.ip_input.setPlaceholderText("IP Address")
        
        self.port_input = QtWidgets.QLineEdit(str(self.DEFAULT_PORT))
        self.port_input.setPlaceholderText("Port")
        self.port_input.setValidator(QtGui.QIntValidator(1, 65535))
        
        conn_settings_layout.addWidget(QtWidgets.QLabel("IP Address:"), 0, 0)
        conn_settings_layout.addWidget(self.ip_input, 0, 1)
        conn_settings_layout.addWidget(QtWidgets.QLabel("Port:"), 1, 0)
        conn_settings_layout.addWidget(self.port_input, 1, 1)
        conn_settings_group.setLayout(conn_settings_layout)
        
        # Connection Status
        self.connection_label = QtWidgets.QLabel("Status: Not Connected")
        self.connection_label.setStyleSheet("font-size: 14px;")
        
        # Status Indicators
        status_group = QtWidgets.QGroupBox("System Status")
        status_layout = QtWidgets.QGridLayout()
        
        self.keyswitch_indicator = self.create_status_light("Keyswitch: Unknown")
        self.init_system_indicator = self.create_status_light("Initial System: Unknown")
        self.loop_system_indicator = self.create_status_light("Looped System: Unknown")
        
        self.init_fault_label = QtWidgets.QLabel("Initial Fault Numbers: -")
        self.loop_fault_label = QtWidgets.QLabel("Looped Fault Numbers: -")
        
        status_layout.addWidget(self.keyswitch_indicator, 0, 0)
        status_layout.addWidget(self.init_system_indicator, 1, 0)
        status_layout.addWidget(self.init_fault_label, 1, 1)
        status_layout.addWidget(self.loop_system_indicator, 2, 0)
        status_layout.addWidget(self.loop_fault_label, 2, 1)
        status_group.setLayout(status_layout)
        
        # Control Panel
        control_group = QtWidgets.QGroupBox("Laser Controls")
        control_layout = QtWidgets.QGridLayout()
        
        self.laser_switch = QtWidgets.QCheckBox("Laser On")
        self.laser_switch.stateChanged.connect(self.laser_switch_changed)
        
        self.shutter_switch = QtWidgets.QCheckBox("Shutter Open")
        self.shutter_switch.stateChanged.connect(self.shutter_switch_changed)
        
        # Replaced QLineEdit with QDoubleSpinBox for power input
        self.power_spinbox = QtWidgets.QDoubleSpinBox()
        self.power_spinbox.setRange(0.0, 18.0)  # Verdi V18 max power
        self.power_spinbox.setDecimals(4)
        self.power_spinbox.setSingleStep(0.1)
        self.power_spinbox.setValue(0.0)
        self.power_spinbox.valueChanged.connect(self.power_changed)
        
        # Powermeter display
        self.powermeter_label = QtWidgets.QLabel("- W")
        self.powermeter_label.setStyleSheet("font-weight: bold;")
        
        # Powermeter average controls
        self.average_control_group = QtWidgets.QGroupBox("Powermeter Statistics")
        average_control_layout = QtWidgets.QGridLayout()
        
        self.average_window_input = QtWidgets.QSpinBox()
        self.average_window_input.setRange(1, 1000)
        self.average_window_input.setValue(10)
        self.average_window_input.valueChanged.connect(self.average_window_changed)
        
        self.average_power_label = QtWidgets.QLabel("- W")
        self.average_power_label.setStyleSheet("font-weight: bold; color: blue;")
        
        self.sigma_label = QtWidgets.QLabel("±- W")
        self.sigma_label.setStyleSheet("font-weight: bold; color: darkred;")
        
        self.readings_count_label = QtWidgets.QLabel("0/10 readings")
        
        average_control_layout.addWidget(QtWidgets.QLabel("Averaging Window Size:"), 0, 0)
        average_control_layout.addWidget(self.average_window_input, 0, 1)
        average_control_layout.addWidget(QtWidgets.QLabel("Average Power:"), 1, 0)
        average_control_layout.addWidget(self.average_power_label, 1, 1)
        average_control_layout.addWidget(QtWidgets.QLabel("Standard Deviation:"), 2, 0)
        average_control_layout.addWidget(self.sigma_label, 2, 1)
        average_control_layout.addWidget(QtWidgets.QLabel("Readings Count:"), 3, 0)
        average_control_layout.addWidget(self.readings_count_label, 3, 1)
        self.average_control_group.setLayout(average_control_layout)
        
        self.connect_pm_btn = QtWidgets.QPushButton("Connect Powermeter")
        self.connect_pm_btn.clicked.connect(self.connect_powermeter)
        
        self.start_btn = QtWidgets.QPushButton("Start Monitoring")
        self.start_btn.clicked.connect(self.start_monitoring)
        
        self.stop_btn = QtWidgets.QPushButton("STOP")
        self.stop_btn.setStyleSheet("background-color: red; color: white;")
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.stop_btn.setEnabled(False)
        
        control_layout.addWidget(QtWidgets.QLabel("Laser State:"), 0, 0)
        control_layout.addWidget(self.laser_switch, 0, 1)
        control_layout.addWidget(QtWidgets.QLabel("Shutter State:"), 1, 0)
        control_layout.addWidget(self.shutter_switch, 1, 1)
        control_layout.addWidget(QtWidgets.QLabel("Requested Power (W):"), 2, 0)
        control_layout.addWidget(self.power_spinbox, 2, 1)
        control_layout.addWidget(QtWidgets.QLabel("Measured Power (W):"), 3, 0)
        control_layout.addWidget(self.powermeter_label, 3, 1)
        control_layout.addWidget(self.average_control_group, 4, 0, 1, 2)
        control_layout.addWidget(self.connect_pm_btn, 5, 0)
        control_layout.addWidget(self.start_btn, 6, 0)
        control_layout.addWidget(self.stop_btn, 6, 1)
        control_group.setLayout(control_layout)
        
        # Measurements Display
        meas_group = QtWidgets.QGroupBox("Measurements")
        meas_layout = QtWidgets.QFormLayout()
        
        self.power_label = QtWidgets.QLabel("- W")
        self.baseplate_temp_label = QtWidgets.QLabel("- °C")
        self.diode1_temp_label = QtWidgets.QLabel("- °C")
        self.diode2_temp_label = QtWidgets.QLabel("- °C")
        self.etalon_temp_label = QtWidgets.QLabel("- °C")
        self.vanadate_temp_label = QtWidgets.QLabel("- °C")
        
        meas_layout.addRow("Output Power:", self.power_label)
        meas_layout.addRow("Baseplate Temp:", self.baseplate_temp_label)
        meas_layout.addRow("Diode 1 Temp:", self.diode1_temp_label)
        meas_layout.addRow("Diode 2 Temp:", self.diode2_temp_label)
        meas_layout.addRow("Etalon Temp:", self.etalon_temp_label)
        meas_layout.addRow("Vanadate Temp:", self.vanadate_temp_label)
        meas_group.setLayout(meas_layout)
        
        # Log Display
        self.log_display = QtWidgets.QTextEdit()
        self.log_display.setReadOnly(True)
        
        # Assemble layout
        layout.addWidget(conn_settings_group)
        layout.addWidget(self.connection_label)
        layout.addWidget(status_group)
        layout.addWidget(control_group)
        layout.addWidget(meas_group)
        layout.addWidget(self.log_display)
        
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        
        # Monitoring thread
        self.monitor_thread = MonitorThread(self)
        self.monitor_thread.update_powermeter.connect(self.update_powermeter_display) 

    def average_window_changed(self):
        """Handle change in averaging window size"""
        new_size = self.average_window_input.value()
        self.average_window_size = new_size
        # Create new deque with updated maxlen
        old_readings = list(self.power_readings)
        self.power_readings = deque(old_readings[-new_size:], maxlen=new_size)
        self.update_average_display()
        self.log(f"Averaging window size changed to {new_size} readings")

    def connect_powermeter(self):
        """Connect to Thorlabs powermeter"""
        try:
            rm = pyvisa.ResourceManager()
            # Try to find the powermeter automatically
            resources = rm.list_resources()
            for res in resources:
                if "USB" in res and "P00" in res:  # Typical Thorlabs pattern
                    self.powermeter = rm.open_resource(res)
                    break
            
            if self.powermeter is None and len(resources) > 0:
                # If not found automatically, try the first device
                self.powermeter = rm.open_resource(resources[0])
            
            if self.powermeter:
                # Test communication
                idn = self.powermeter.query("*IDN?")
                self.log(f"Connected to powermeter: {idn.strip()}")
                self.powermeter_connected = True
                self.connect_pm_btn.setText("Powermeter Connected")
                self.connect_pm_btn.setStyleSheet("background-color: green; color: white;")
            else:
                self.log("No powermeter found")
                self.powermeter_connected = False
                self.connect_pm_btn.setText("Connect Powermeter")
                self.connect_pm_btn.setStyleSheet("")
        except Exception as e:
            self.log(f"Powermeter connection error: {str(e)}")
            self.powermeter_connected = False
            self.connect_pm_btn.setText("Connect Powermeter")
            self.connect_pm_btn.setStyleSheet("")

    def get_powermeter_reading(self):
        """Get current power reading from Thorlabs powermeter"""
        if not self.powermeter_connected or self.powermeter is None:
            return None
        
        try:
            power = self.powermeter.query("MEAS:POW?")
            return float(power.strip())
        except Exception as e:
            self.log(f"Powermeter reading error: {str(e)}")
            self.powermeter_connected = False
            self.connect_pm_btn.setText("Connect Powermeter")
            self.connect_pm_btn.setStyleSheet("")
            return None

    def create_status_light(self, text):
        """Create a status indicator with label"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        
        label = QtWidgets.QLabel(text)
        light = QtWidgets.QLabel()
        light.setFixedSize(20, 20)
        light.setStyleSheet("background-color: gray; border-radius: 10px;")
        
        layout.addWidget(light)
        layout.addWidget(label)
        layout.addStretch()
        widget.setLayout(layout)
        
        # Store reference to the light
        light_name = f"{text.split(':')[0].lower().replace(' ', '_')}_light"
        setattr(self, light_name, light)
        return widget

    def update_status_light(self, light, state):
        """Update a status light color"""
        color = "green" if state else "red"
        light.setStyleSheet(f"background-color: {color}; border-radius: 10px;")

    def prompt_logging(self):
        """Ask user if they want to enable logging"""
        reply = QtWidgets.QMessageBox.question(
            self, 'Logging', 
            'Do you want to enable data logging?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            options = QtWidgets.QFileDialog.Options()
            file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Select Log File", "", "Text Files (*.txt);;All Files (*)", options=options)
            
            if file_path:
                try:
                    self.log_file = open(file_path, 'a')
                    self.logging_enabled = True
                    self.log("Logging enabled, file created")
                    # Write header
                    self.log_file.write("Timestamp,Fault Status,Output Power,Baseplate Temp,Diode1 Temp,Diode2 Temp,Etalon Temp,Vanadate Temp,Measured Power,Average Power,Standard Deviation\n")
                except Exception as e:
                    self.log(f"Could not create log file: {str(e)}")
                    self.logging_enabled = False
        else:
            self.log("Logging disabled")

    def connect_laser(self):
        """Establish connection to laser"""
        try:
            # Get current IP and port from UI
            self.HOST = self.ip_input.text() or self.DEFAULT_HOST
            self.PORT = int(self.port_input.text() or self.DEFAULT_PORT)
            
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)  # Increased timeout for connection
            self.sock.connect((self.HOST, self.PORT))
            self.connection_label.setText(f"Status: Connected to {self.HOST}:{self.PORT}")
            self.connection_label.setStyleSheet("color: green;")
            self.log(f"Connected to laser at {self.HOST}:{self.PORT}")
            return True
        except Exception as e:
            self.connection_label.setText(f"Status: Error ({str(e)})")
            self.connection_label.setStyleSheet("color: red;")
            self.log(f"Connection error: {str(e)}")
            return False

    def send_command(self, command):
        """Send command to laser and return cleaned response"""
        try:
            if not self.sock:
                if not self.connect_laser():
                    return None
                
            # Send command
            self.sock.sendall((command + "\r\n").encode('ascii'))
            
            # Receive response with timeout
            response = b""
            while True:
                part = self.sock.recv(1024)
                if not part:
                    break
                response += part
                if b"\r\n" in response:  # Check for end of message
                    break
            
            # Clean response
            clean_response = response.decode('ascii').strip()
            clean_response = clean_response.replace("\r", "").replace("\n", "")
            
            self.log(f"Sent: {command} | Received: {clean_response}")
            return clean_response
        except socket.timeout:
            self.log(f"Timeout waiting for response to command: {command}")
            return None
        except Exception as e:
            self.log(f"Command error: {str(e)}")
            return None

    def initialize_laser(self):
        """Perform initial checks"""
        # Check keyswitch
        response = self.send_command("?K")
        if response == "?K1":
            self.keyswitch_enabled = True
            self.update_status_light(self.keyswitch_light, True)
            self.log("Keyswitch enabled")
        else:
            self.keyswitch_enabled = False
            self.update_status_light(self.keyswitch_light, False)
            self.log("Keyswitch disabled - stopping")
            return False
        
        # Check initial faults
        response = self.send_command("?F")
        if response == "?FSystem OK":
            self.system_ok = True
            self.update_status_light(self.initial_system_light, True)
            self.init_fault_label.setText("Initial Fault Numbers: System OK")
            self.log("Initial system check OK")
        else:
            self.system_ok = False
            self.update_status_light(self.initial_system_light, False)
            fault_nums = response[2:] if response.startswith("?F") else response
            self.init_fault_label.setText(f"Initial Fault Numbers: {fault_nums}")
            self.log(f"Initial faults detected: {fault_nums}")
        
        return True

    def start_monitoring(self):
        """Start the monitoring loop"""
        if not self.connect_laser():
            return
            
        if not self.initialize_laser():
            self.stop_monitoring()
            return
            
        self.running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log("Starting monitoring loop")
        
        # Clear previous readings
        self.power_readings.clear()
        self.update_average_display()
        
        # Start monitoring thread
        self.monitor_thread.start()

    def stop_monitoring(self):
        """Stop the monitoring loop"""
        self.running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log("Stopping monitoring")
        
        if self.monitor_thread.isRunning():
            self.monitor_thread.quit()
            self.monitor_thread.wait()
        
        if self.sock:
            self.sock.close()
            self.sock = None
            self.connection_label.setText("Status: Disconnected")
            self.connection_label.setStyleSheet("color: red;")

    def laser_switch_changed(self, state):
        """Handle laser on/off switch change"""
        self.laser_on = (state == QtCore.Qt.Checked)
        self.log(f"Laser {'ON' if self.laser_on else 'OFF'} requested")

    def shutter_switch_changed(self, state):
        """Handle shutter open/close switch change"""
        self.shutter_on = (state == QtCore.Qt.Checked)
        self.log(f"Shutter {'OPEN' if self.shutter_on else 'CLOSED'} requested")

    def power_changed(self):
        """Handle power input change"""
        self.requested_power = self.power_spinbox.value()
        self.log(f"Requested power changed to {self.requested_power:.4f} W")

    def log(self, message, timestamp=True):
        """Add message to log display"""
        if timestamp:
            message = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.log_display.append(message)
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum())

    def log_data(self, data):
        """Log measurement data to file"""
        if self.logging_enabled and self.log_file:
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Calculate statistics if we have readings
                if len(self.power_readings) > 0:
                    avg_power = sum(self.power_readings) / len(self.power_readings)
                    data.append(f"{avg_power:.6f}")
                    if len(self.power_readings) > 1:
                        sigma = statistics.stdev(self.power_readings)
                        data.append(f"{sigma:.6f}")
                    else:
                        data.append("")  # Not enough data for sigma
                else:
                    data.extend(["", ""])  # No readings for avg and sigma
                
                self.log_file.write(f"{timestamp},{','.join(data)}\n")
                self.log_file.flush()
            except Exception as e:
                self.log(f"Logging error: {str(e)}")

    def closeEvent(self, event):
        """Cleanup on exit"""
        self.stop_monitoring()
        if self.log_file:
            self.log_file.close()
        if self.powermeter:
            self.powermeter.close()
        event.accept()

class MonitorThread(QThread):
    """Thread for monitoring laser parameters"""
    update_measurements = pyqtSignal(dict)
    update_powermeter = pyqtSignal(float)
    
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.last_laser_state = None
        self.last_shutter_state = None
        self.last_power = None

    def run(self):
        while self.controller.running:
            start_time = time.time()
            data = []
            
            # 1. Check faults
            response = self.controller.send_command("?F")
            if response == "?FSystem OK":
                self.controller.system_ok = True
                self.controller.update_status_light(self.controller.looped_system_light, True)
                self.controller.loop_fault_label.setText("Looped Fault Numbers: System OK")
                data.append("OK")
            else:
                self.controller.system_ok = False
                self.controller.update_status_light(self.controller.looped_system_light, False)
                fault_nums = response[2:] if response.startswith("?F") else response
                self.controller.loop_fault_label.setText(f"Looped Fault Numbers: {fault_nums}")
                data.append(fault_nums)
            
            # 2. Handle laser/shutter commands if changed
            if (self.last_laser_state != self.controller.laser_on or 
                self.last_shutter_state != self.controller.shutter_on):
                
                cmd = f"L={1 if self.controller.laser_on else 0}"
                response = self.controller.send_command(cmd)
                
                cmd = f"S={1 if self.controller.shutter_on else 0}"
                response = self.controller.send_command(cmd)
                
                self.last_laser_state = self.controller.laser_on
                self.last_shutter_state = self.controller.shutter_on
            else:
                time.sleep(0.2)  # Wait 200ms if no changes
            
            # 3. Set power if changed
            if self.last_power != self.controller.requested_power:
                cmd = f"P={self.controller.requested_power:.4f}"
                response = self.controller.send_command(cmd)
                if response:
                    data.append(response.strip())
                self.last_power = self.controller.requested_power
            
            # 4. Get measurements
            measurements = {}
            
            # Output power
            response = self.controller.send_command("?SP")
            if response and response.startswith("?SP"):
                power = response[3:].strip()
                self.controller.power_label.setText(f"{power} W")
                measurements['power'] = power
                data.append(power)
            
            # Baseplate temperature
            response = self.controller.send_command("?BT")
            if response and response.startswith("?BT"):
                temp = response[3:].strip()
                self.controller.baseplate_temp_label.setText(f"{temp} °C")
                measurements['baseplate_temp'] = temp
                data.append(temp)
            
            # Diode 1 temperature
            response = self.controller.send_command("?D1T")
            if response and response.startswith("?D1T"):
                temp = response[4:].strip()
                self.controller.diode1_temp_label.setText(f"{temp} °C")
                measurements['diode1_temp'] = temp
                data.append(temp)
            
            # Diode 2 temperature
            response = self.controller.send_command("?D2T")
            if response and response.startswith("?D2T"):
                temp = response[4:].strip()
                self.controller.diode2_temp_label.setText(f"{temp} °C")
                measurements['diode2_temp'] = temp
                data.append(temp)
            
            # Etalon temperature
            response = self.controller.send_command("?ET")
            if response and response.startswith("?ET"):
                temp = response[3:].strip()
                self.controller.etalon_temp_label.setText(f"{temp} °C")
                measurements['etalon_temp'] = temp
                data.append(temp)
            
            # Vanadate temperature
            response = self.controller.send_command("?VT")
            if response and response.startswith("?VT"):
                temp = response[3:].strip()
                self.controller.vanadate_temp_label.setText(f"{temp} °C")
                measurements['vanadate_temp'] = temp
                data.append(temp)
            
            # Get powermeter reading
            measured_power = self.controller.get_powermeter_reading()
            if measured_power is not None:
                self.update_powermeter.emit(measured_power)  # Emit the signal
                data.append(f"{measured_power:.6f}")
            else:
                data.append("")
            
            # Emit measurements and log data
            self.update_measurements.emit(measurements)
            if len(data) == 8:  # All measurements received (including powermeter)
                self.controller.log_data(data)
            
            # Ensure loop takes ~2000ms
            elapsed = time.time() - start_time
            if elapsed < 2.0:
                time.sleep(2.0 - elapsed)

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = VerdiLaserController()
    window.show()
    app.exec_()