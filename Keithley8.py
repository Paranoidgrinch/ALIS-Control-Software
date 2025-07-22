import socket
import time
import numpy as np
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import sys
import os
from opcua import Client

class GaugeWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(600, 600)
        self.value = 0
        self.min_value = 0
        self.max_value = 100
        self.unit = "nA"
        
        # Colors
        self.gauge_color = QtGui.QColor(240, 240, 240)  # Light gray background
        self.value_color = QtGui.QColor(0, 150, 255)    # Blue value arc
        self.text_color = QtGui.QColor(0, 0, 0)         # Black text
        self.needle_color = QtGui.QColor(255, 50, 50)   # Red needle
    
    def set_range(self, min_val, max_val, unit):
        self.min_value = min_val
        self.max_value = max_val
        self.unit = unit
        self.update()
        
    def set_value(self, value):
        self.value = value
        self.update()
        
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        
        # Dimensions
        width = self.width()
        height = self.height() 
        painter.translate(width / 2, height / 2)
        scale = min(width / 200, height / 100)
        painter.scale(scale, scale)
        
        # Constants for horizontal gauge (180° to 0°)
        start_angle = 180  # Left side
        end_angle = 0      # Right side
        span_angle = 180   # Total sweep angle
        
        # Calculate current angle (0° at right, 180° at left)
        value_angle = start_angle - (span_angle * (self.value - self.min_value) / 
                                   (self.max_value - self.min_value))
        value_angle = max(end_angle, min(start_angle, value_angle))  # Clamp angle
        
        # Draw gauge background
        painter.setPen(QtGui.QPen(self.gauge_color, 2))
        painter.setBrush(self.gauge_color)
        painter.drawEllipse(-90, -90, 180, 180)  # Wide elliptical shape
        painter.drawChord(-90, -90, 180, 180, 0 * 16, -180 * 16)

        # Draw base arc
        painter.setPen(QtGui.QPen(QtGui.QColor(100, 100, 100), 3))
        painter.drawArc(-90, -90, 180, 180, 180 * 16, -180 * 16)
        
        # Draw value arc
        painter.setPen(QtGui.QPen(self.value_color, 3))
        painter.drawArc(-90, -90, 180, 180, 
                       start_angle * 16, 
                       int((value_angle - start_angle) * 16))
        
        # Draw needle
        needle_angle = np.radians(value_angle)
        needle_length = 80
        x = int(round(needle_length * np.cos(needle_angle)))
        y = int(round(needle_length * np.sin(needle_angle)))
        
        painter.setPen(QtGui.QPen(self.needle_color, 2))
        painter.setBrush(self.needle_color)
        painter.drawLine(0, 0, x, -y)  # Needle points at current value
        painter.drawEllipse(-5, -5, 10, 10)  # Needle center
        
        # Draw scale markers (11 ticks from left to right)
        painter.setPen(QtGui.QPen(self.text_color, 2))
        for i in range(0, 11):
            angle = start_angle - (i * (span_angle / 10))  # Evenly spaced ticks
            rad = np.radians(angle)
            inner = 80
            outer = 100
            
            # Calculate marker positions
            x1 = int(round(inner * np.cos(rad)))
            y1 = int(round(inner * np.sin(rad)))
            x2 = int(round(outer * np.cos(rad)))
            y2 = int(round(outer * np.sin(rad)))
            
            painter.drawLine(x1, -y1, x2, -y2)
            
            # Draw scale values at even intervals
            if i % 2 == 0:
                value = self.min_value + (i / 10) * (self.max_value - self.min_value)
                text = f"{value:.0f}" if (self.max_value - self.min_value) > 10 else f"{value:.1f}"
                text_x = int(round((outer - 30) * np.cos(rad)))
                text_y = int(round((outer - 30) * np.sin(rad)))
                
                font = painter.font()
                font.setPointSize(6)
                painter.setFont(font)
                painter.drawText(QtCore.QPointF(text_x, -text_y), text)
        
        # Draw current value display (centered above needle)
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(self.text_color))
        value_text = f"{self.value:.2f} {self.unit}"
        painter.drawText(QtCore.QRectF(-40, -30, 80, 20), 
                         QtCore.Qt.AlignCenter, value_text)
        
        

class KeithleyMonitor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Keithley 6485 Nanoampere Monitor")
        self.resize(1200, 800)
        
        # Measurement state
        self.is_measuring = False
        self.start_time = None
        self.log_file = None
        self.current_range_index = 5  # Default to 0-100nA range
        self.ranges = [
            (0, 100, "pA"), (0, 300, "pA"), (0, 1, "nA"), (0, 3, "nA"),
            (0, 10, "nA"), (0, 30, "nA"), (0, 100, "nA"), (0, 300, "nA"),
            (0, 1, "µA"), (0, 3, "µA"), (0, 10, "µA"), (0, 30, "µA")
        ]
        
        # Data storage
        self.timestamps = []
        self.measurements_nA = []
        self.charge_nC = 0.0  # Integrated charge in nanocoulombs
        self.filter_threshold = 100000  # 100 µA in nA
        self.plot_start_time = 0  # Time when plot was last cleared
        
        # Create main widget and tab system
        self.tabs = QtWidgets.QTabWidget()
        
        # Create tabs
        self.create_main_tab()
        self.create_gauge_tab()
        
        # Set central widget
        self.setCentralWidget(self.tabs)
        
        # Status bar
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # TCP connection for Keithley
        self.HOST = "192.168.0.2"
        self.PORT = 100
        self.sock = None
        
        # OPC UA connection for sputter current
        self.opc_url = "opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2"
        self.opc_node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Sputter_I"
        self.opc_client = None
        self.opc_node = None
        self.last_sputter_current = 0.0
        
        # Setup timers
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_measurement)
        
        self.sputter_timer = QtCore.QTimer()
        self.sputter_timer.timeout.connect(self.update_sputter_current)
        self.sputter_timer.start(1000)  # Update sputter current every second
    
    def create_main_tab(self):
        """Create the main measurement tab"""
        main_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        
        # Control panel
        control_panel = QtWidgets.QGroupBox("Controls")
        control_layout = QtWidgets.QGridLayout()
        
        # File selection
        self.file_label = QtWidgets.QLabel("No file selected")
        browse_button = QtWidgets.QPushButton("Select Log File")
        browse_button.clicked.connect(self.select_log_file)
        
        # Measurement controls
        self.start_button = QtWidgets.QPushButton("Start Measurement")
        self.start_button.clicked.connect(self.toggle_measurement)
        self.start_button.setStyleSheet("background-color: #4CAF50; color: white;")
        
        self.stop_button = QtWidgets.QPushButton("Stop Measurement")
        self.stop_button.clicked.connect(self.toggle_measurement)
        self.stop_button.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_button.setEnabled(False)
        
        # Clear graph button
        self.clear_button = QtWidgets.QPushButton("Clear Graph")
        self.clear_button.clicked.connect(self.clear_graph)
        self.clear_button.setStyleSheet("background-color: #FFA500; color: white;")
        
        # Interval control
        interval_label = QtWidgets.QLabel("Measurement Interval (s):")
        self.interval_spin = QtWidgets.QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 60)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setSingleStep(0.1)
        
        # Filter checkbox
        self.filter_checkbox = QtWidgets.QCheckBox("Filter values > 100 µA")
        self.filter_checkbox.setChecked(False)
        
        # Add widgets to layout
        control_layout.addWidget(QtWidgets.QLabel("Log File:"), 0, 0)
        control_layout.addWidget(self.file_label, 0, 1)
        control_layout.addWidget(browse_button, 0, 2)
        control_layout.addWidget(self.start_button, 1, 0, 1, 3)
        control_layout.addWidget(self.stop_button, 2, 0, 1, 3)
        control_layout.addWidget(self.clear_button, 3, 0, 1, 3)
        control_layout.addWidget(interval_label, 4, 0)
        control_layout.addWidget(self.interval_spin, 4, 1)
        control_layout.addWidget(self.filter_checkbox, 5, 0, 1, 3)
        
        control_panel.setLayout(control_layout)
        layout.addWidget(control_panel)
        
        # Value displays
        value_display_layout = QtWidgets.QHBoxLayout()
        
        # Current display
        self.value_display = QtWidgets.QLabel("Current: ---")
        self.value_display.setStyleSheet("font-size: 24px; font-weight: bold; color: #2E86C1;")
        self.value_display.setAlignment(QtCore.Qt.AlignCenter)
        
        # Charge display
        self.charge_display = QtWidgets.QLabel("Charge: 0.00 nC")
        self.charge_display.setStyleSheet("font-size: 24px; font-weight: bold; color: #27AE60;")
        self.charge_display.setAlignment(QtCore.Qt.AlignCenter)
        
        # Sputter current display
        self.sputter_display = QtWidgets.QLabel("Sputter: --- mA")
        self.sputter_display.setStyleSheet("font-size: 24px; font-weight: bold; color: #8E44AD;")
        self.sputter_display.setAlignment(QtCore.Qt.AlignCenter)
        
        value_display_layout.addWidget(self.value_display)
        value_display_layout.addWidget(self.charge_display)
        value_display_layout.addWidget(self.sputter_display)
        layout.addLayout(value_display_layout)
        
        # Matplotlib plot
        self.figure = Figure(figsize=(10, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        layout.addWidget(self.canvas)
        
        main_tab.setLayout(layout)
        self.tabs.addTab(main_tab, "Main")
    
    def create_gauge_tab(self):
        """Create the gauge display tab"""
        gauge_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        
        # Range selection
        range_group = QtWidgets.QGroupBox("Current Range")
        range_layout = QtWidgets.QGridLayout()
        
        # Create range buttons
        self.range_buttons = []
        for i, (min_val, max_val, unit) in enumerate(self.ranges):
            btn = QtWidgets.QPushButton(f"{max_val} {unit}")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, idx=i: self.set_range(idx))
            self.range_buttons.append(btn)
            range_layout.addWidget(btn, i//3, i%3)
        
        # Set default range button as checked
        self.range_buttons[self.current_range_index].setChecked(True)
        
        range_group.setLayout(range_layout)
        layout.addWidget(range_group)
        
        # Create gauge
        self.gauge = GaugeWidget()
        self.gauge.set_range(0, 100, "nA")  # Initial range
        
        # Add to layout
        gauge_layout = QtWidgets.QVBoxLayout()
        gauge_layout.addWidget(self.gauge, 0, QtCore.Qt.AlignCenter)
        
        layout.addLayout(gauge_layout)
        gauge_tab.setLayout(layout)
        self.tabs.addTab(gauge_tab, "Gauge")
    
    def clear_graph(self):
        """Clear the graph while continuing measurements"""
        if self.is_measuring and len(self.timestamps) > 0:
            self.plot_start_time = self.timestamps[-1]
            self.update_plot()
    
    def set_range(self, index):
        """Set the current range for the gauge"""
        self.current_range_index = index
        min_val, max_val, unit = self.ranges[index]
        
        for i, btn in enumerate(self.range_buttons):
            btn.setChecked(i == index)
        
        # Update gauge range
        self.gauge.set_range(min_val, max_val, unit)
        
        # Update the gauge display if we have a current measurement
        if self.measurements_nA:
            self.update_gauge(self.measurements_nA[-1])
    
    def update_gauge(self, current_nA):
        """Update the gauge display with the current measurement"""
        min_val, max_val, unit = self.ranges[self.current_range_index]
        
        # Convert current to the selected unit
        if unit == "pA":
            display_value = current_nA * 1000
        elif unit == "nA":
            display_value = current_nA
        elif unit == "µA":
            display_value = current_nA / 1000
        else:
            display_value = current_nA
        
        # Clamp the value to the gauge range
        clamped_value = max(min_val, min(max_val, display_value))
        self.gauge.set_value(clamped_value)
    
    def select_log_file(self):
        """Prompt user to select a log file location"""
        options = QtWidgets.QFileDialog.Options()
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Select Log File", "", "Text Files (*.txt);;All Files (*)", options=options)
        
        if file_path:
            self.log_file = file_path
            self.file_label.setText(os.path.basename(file_path))
    
    def toggle_measurement(self):
        """Start or stop measurements"""
        if not self.is_measuring:
            # Start measurement
            if not self.log_file:
                QtWidgets.QMessageBox.warning(self, "Warning", "Please select a log file first")
                return
                
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect((self.HOST, self.PORT))
                self.initialize_keithley()
                
                # Initialize OPC UA connection
                try:
                    self.opc_client = Client(self.opc_url)
                    self.opc_client.connect()
                    self.opc_node = self.opc_client.get_node(self.opc_node_id)
                except Exception as opc_error:
                    print(f"OPC UA connection failed: {opc_error}")
                
                # Clear previous data
                self.timestamps = []
                self.measurements_nA = []
                self.charge_nC = 0.0
                self.start_time = time.time()
                self.plot_start_time = 0
                
                # Open log file
                with open(self.log_file, 'w') as f:
                    f.write("Timestamp,Elapsed Time (s),Current (nA),Charge (nC),Sputter Current (mA)\n")
                
                self.is_measuring = True
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(True)
                self.clear_button.setEnabled(True)
                
                # Start timer
                self.timer.start(int(self.interval_spin.value() * 1000))
                
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Connection failed: {str(e)}")
                if self.sock:
                    self.sock.close()
                if self.opc_client:
                    self.opc_client.disconnect()
        else:
            # Stop measurement
            self.is_measuring = False
            self.timer.stop()
            if self.sock:
                self.sock.close()
            if self.opc_client:
                self.opc_client.disconnect()
            
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.clear_button.setEnabled(False)
    
    def initialize_keithley(self):
        """Initialize Keithley 6485"""
        init_commands = [
            "*RST", ":SYST:ZCH OFF", ":SYST:ZCOR ON",
            ":FORM:ELEM READ", ":SENS:CURR:RANG:AUTO ON",
            ":SENS:CURR:NPLC 1"
        ]
        
        for cmd in init_commands:
            self.send_command(cmd, 0.5)
    
    def send_command(self, command, wait_time=0.1):
        """Send command to Keithley"""
        try:
            self.sock.sendall((command + "\n").encode('ascii'))
            time.sleep(wait_time)
            if command.endswith("?"):
                response = self.sock.recv(1024).decode('ascii').strip()
                if '\n' in response:
                    response = response.split('\n')[0]
                return response
            return None
        except Exception as e:
            print(f"Command error: {e}")
            return None
    
    def update_sputter_current(self):
        """Update the sputter current from OPC UA server"""
        if not self.opc_client or not self.opc_node:
            return
            
        try:
            sputter_current = self.opc_node.get_value()
            self.last_sputter_current = float(sputter_current) 
            self.sputter_display.setText(f"Sputter: {self.last_sputter_current:.2f} mA")
        except Exception as e:
            print(f"OPC UA error: {e}")
            self.sputter_display.setText("Sputter: --- mA")
    
    def update_measurement(self):
        """Take a new measurement and update display"""
        try:
            response = self.send_command("READ?", 0.5)
            if not response:
                return
                
            try:
                if '\n' in response:
                    response = response.split('\n')[0]
                
                current_A = float(response.replace(',', '.'))
                current_nA = abs(current_A) * 1e9
                
                # Apply filter if enabled
                if self.filter_checkbox.isChecked() and current_nA > self.filter_threshold:
                    print(f"Filtered out value: {current_nA:.2f} nA (above threshold)")
                    return
                
                elapsed = time.time() - self.start_time
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                
                # Calculate charge using trapezoidal rule (Q = ∫I dt)
                if len(self.timestamps) > 0:
                    dt = elapsed - self.timestamps[-1]
                    # Area of trapezoid: (I1 + I2)/2 * dt (converted to nC)
                    self.charge_nC += (self.measurements_nA[-1] + current_nA)/2 * dt
                
                self.timestamps.append(elapsed)
                self.measurements_nA.append(current_nA)
                
                # Update displays
                self.value_display.setText(f"Current: {current_nA:.2f} nA")
                self.charge_display.setText(f"Charge: {self.charge_nC:.2f} nC")
                self.update_gauge(current_nA)
                self.update_plot()
                
                # Update log file
                with open(self.log_file, 'a') as f:
                    f.write(f"{timestamp},{elapsed:.3f},{current_nA:.2f},{self.charge_nC:.2f},{self.last_sputter_current:.2f}\n")
                    
            except ValueError as e:
                print(f"Value conversion error: {e}")
        except Exception as e:
            print(f"Measurement error: {e}")
            self.timer.stop()
            self.is_measuring = False
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.clear_button.setEnabled(False)
            QtWidgets.QMessageBox.critical(self, "Error", f"Measurement failed: {str(e)}")
    
    def update_plot(self):
        """Update the plot with current data"""
        self.ax.clear()
        
        if len(self.timestamps) > 0:
            # Filter data points after plot_start_time
            plot_times = [t - self.plot_start_time for t in self.timestamps if t >= self.plot_start_time]
            plot_values = [v for t, v in zip(self.timestamps, self.measurements_nA) if t >= self.plot_start_time]
            
            if plot_times:
                self.ax.plot(plot_times, plot_values, 'b-')
                self.ax.set_xlabel('Elapsed Time (s)')
                self.ax.set_ylabel('Current (nA)', color='b')
                self.ax.grid(True)
                
                if len(plot_times) > 1:
                    self.ax.set_xlim(min(plot_times), max(plot_times))
                    self.ax.set_ylim(0, max(plot_values) * 1.1)
        
        self.canvas.draw()
    
    def closeEvent(self, event):
        """Handle window close"""
        if self.is_measuring:
            self.toggle_measurement()
        if self.sock:
            self.sock.close()
        if self.opc_client:
            self.opc_client.disconnect()
        event.accept()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = KeithleyMonitor()
    window.show()
    sys.exit(app.exec_())