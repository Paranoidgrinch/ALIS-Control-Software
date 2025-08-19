# Keithley 6485 Nanoampere Monitor with live plotting, gauge view, and OPC UA sputter-current readout.
# - Talks to Keithley over TCP (SCPI) for current measurements.
# - Optionally reads sputter current via OPC UA (for context alongside nanoamp readings).
# - Logs timestamped data, integrates charge (nC), shows moving average/stddev, and provides a gauge UI.

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
    # Minimal, self-painted horizontal half-circle gauge for displaying the current
    # in a chosen unit range. The Keithley tab updates this widget via set_range/set_value.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(600, 600)
        self.value = 0
        self.min_value = 0
        self.max_value = 100
        self.unit = "nA"

        # Color palette for gauge background, value arc, text, and needle
        self.gauge_color = QtGui.QColor(240, 240, 240)
        self.value_color = QtGui.QColor(0, 150, 255)
        self.text_color = QtGui.QColor(0, 0, 0)
        self.needle_color = QtGui.QColor(255, 50, 50)

    def set_range(self, min_val, max_val, unit):
        # Change scale and unit together (called when range buttons change)
        self.min_value = min_val
        self.max_value = max_val
        self.unit = unit
        self.update()

    def set_value(self, value):
        # Update the instantaneous value shown on the gauge
        self.value = value
        self.update()

    def paintEvent(self, event):
        # Custom paint: draw a semicircular dial, ticks, value arc, needle, and numeric text
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        # Normalize drawing space to a fixed coordinate system
        width = self.width()
        height = self.height()
        painter.translate(width / 2, height / 2)
        scale = min(width / 200, height / 100)
        painter.scale(scale, scale)

        # Horizontal gauge from 180° (left) to 0° (right)
        start_angle = 180
        end_angle = 0
        span_angle = 180

        # Map current value to angle and clamp to range
        value_angle = start_angle - (span_angle * (self.value - self.min_value) /
                                     (self.max_value - self.min_value))
        value_angle = max(end_angle, min(start_angle, value_angle))

        # Background semicircle
        painter.setPen(QtGui.QPen(self.gauge_color, 2))
        painter.setBrush(self.gauge_color)
        painter.drawEllipse(-90, -90, 180, 180)
        painter.drawChord(-90, -90, 180, 180, 0 * 16, -180 * 16)

        # Base (full range) arc
        painter.setPen(QtGui.QPen(QtGui.QColor(100, 100, 100), 3))
        painter.drawArc(-90, -90, 180, 180, 180 * 16, -180 * 16)

        # Value arc from left to the current angle
        painter.setPen(QtGui.QPen(self.value_color, 3))
        painter.drawArc(-90, -90, 180, 180,
                        start_angle * 16,
                        int((value_angle - start_angle) * 16))

        # Needle pointing to current angle
        needle_angle = np.radians(value_angle)
        needle_length = 80
        x = int(round(needle_length * np.cos(needle_angle)))
        y = int(round(needle_length * np.sin(needle_angle)))

        painter.setPen(QtGui.QPen(self.needle_color, 2))
        painter.setBrush(self.needle_color)
        painter.drawLine(0, 0, x, -y)
        painter.drawEllipse(-5, -5, 10, 10)

        # Major/minor ticks + numeric labels
        painter.setPen(QtGui.QPen(self.text_color, 2))
        for i in range(0, 11):
            angle = start_angle - (i * (span_angle / 10))
            rad = np.radians(angle)
            inner = 80
            outer = 100

            x1 = int(round(inner * np.cos(rad)))
            y1 = int(round(inner * np.sin(rad)))
            x2 = int(round(outer * np.cos(rad)))
            y2 = int(round(outer * np.sin(rad)))
            painter.drawLine(x1, -y1, x2, -y2)

            if i % 2 == 0:
                value = self.min_value + (i / 10) * (self.max_value - self.min_value)
                text = f"{value:.0f}" if (self.max_value - self.min_value) > 10 else f"{value:.1f}"
                text_x = int(round((outer - 30) * np.cos(rad)))
                text_y = int(round((outer - 30) * np.sin(rad)))
                font = painter.font()
                font.setPointSize(6)
                painter.setFont(font)
                painter.drawText(QtCore.QPointF(text_x, -text_y), text)

        # Numeric readout at center
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(self.text_color))
        value_text = f"{self.value:.2f} {self.unit}"
        painter.drawText(QtCore.QRectF(-40, -30, 80, 20),
                         QtCore.Qt.AlignCenter, value_text)


class KeithleyMonitor(QtWidgets.QMainWindow):
    # Main window:
    # - Main tab: start/stop acquisition, moving average, filter, live plot, charge integration, sputter readout
    # - Gauge tab: selectable ranges & large analog-style gauge
    # - Connectivity: TCP to Keithley; optional OPC UA to read sputter current
    # - Logging: CSV with timestamp, elapsed time, current (nA), charge (nC), sputter current (mA)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Keithley 6485 Nanoampere Monitor")
        self.resize(1200, 800)

        # Measurement state and configuration (lifecycle, logging, moving-average window)
        self.is_measuring = False
        self.start_time = None
        self.log_file = None
        self.current_range_index = 5  # Default gauge range button (0-30 nA)
        self.ranges = [
            (0, 100, "pA"), (0, 300, "pA"), (0, 1, "nA"), (0, 3, "nA"),
            (0, 10, "nA"), (0, 30, "nA"), (0, 100, "nA"), (0, 300, "nA"),
            (0, 1, "µA"), (0, 3, "µA"), (0, 10, "µA"), (0, 30, "µA")
        ]

        # Time series buffers (nA, seconds); integrated charge tracked in nC via trapezoidal rule
        self.timestamps = []
        self.measurements_nA = []
        self.charge_nC = 0.0
        self.filter_threshold = 100000  # 100 µA expressed as nA
        self.plot_start_time = 0

        # Moving-average window (last N samples)
        self.window_size = 10

        # UI scaffold: two tabs (main + gauge)
        self.tabs = QtWidgets.QTabWidget()
        self.create_main_tab()
        self.create_gauge_tab()
        self.setCentralWidget(self.tabs)

        # Status bar for brief status text
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

        # Instrument endpoints
        self.HOST = "192.168.0.2"
        self.PORT = 100
        self.sock = None

        # OPC UA sputter-current readback (optional context signal)
        self.opc_url = "opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2"
        self.opc_node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Sputter_I"
        self.opc_client = None
        self.opc_node = None
        self.last_sputter_current = 0.0

        # Timers: measurement cadence and independent sputter-current polling
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_measurement)

        self.sputter_timer = QtCore.QTimer()
        self.sputter_timer.timeout.connect(self.update_sputter_current)
        self.sputter_timer.start(1000)  # 1 Hz sputter read

    def create_main_tab(self):
        # Main acquisition tab: file selection, start/stop, interval, moving average, filter,
        # live numeric displays, and a matplotlib time plot
        main_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        # ---- Controls ----
        control_panel = QtWidgets.QGroupBox("Controls")
        control_layout = QtWidgets.QGridLayout()

        # Log file picker
        self.file_label = QtWidgets.QLabel("No file selected")
        browse_button = QtWidgets.QPushButton("Select Log File")
        browse_button.clicked.connect(self.select_log_file)

        # Start/Stop
        self.start_button = QtWidgets.QPushButton("Start Measurement")
        self.start_button.clicked.connect(self.toggle_measurement)
        self.start_button.setStyleSheet("background-color: #4CAF50; color: white;")

        self.stop_button = QtWidgets.QPushButton("Stop Measurement")
        self.stop_button.clicked.connect(self.toggle_measurement)
        self.stop_button.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_button.setEnabled(False)

        # Clear plot (retains ongoing measurement; only resets x-origin)
        self.clear_button = QtWidgets.QPushButton("Clear Graph")
        self.clear_button.clicked.connect(self.clear_graph)
        self.clear_button.setStyleSheet("background-color: #FFA500; color: white;")

        # Cadence (s)
        interval_label = QtWidgets.QLabel("Measurement Interval (s):")
        self.interval_spin = QtWidgets.QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 60)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setSingleStep(0.1)

        # Moving average window length
        avg_label = QtWidgets.QLabel("Moving Average Window:")
        self.avg_spin = QtWidgets.QSpinBox()
        self.avg_spin.setRange(1, 1000)
        self.avg_spin.setValue(10)
        self.avg_spin.valueChanged.connect(self.update_window_size)

        # Optional outlier filter (reject > 100 µA)
        self.filter_checkbox = QtWidgets.QCheckBox("Filter values > 100 µA")
        self.filter_checkbox.setChecked(False)

        # Controls layout
        control_layout.addWidget(QtWidgets.QLabel("Log File:"), 0, 0)
        control_layout.addWidget(self.file_label, 0, 1)
        control_layout.addWidget(browse_button, 0, 2)
        control_layout.addWidget(self.start_button, 1, 0, 1, 3)
        control_layout.addWidget(self.stop_button, 2, 0, 1, 3)
        control_layout.addWidget(self.clear_button, 3, 0, 1, 3)
        control_layout.addWidget(interval_label, 4, 0)
        control_layout.addWidget(self.interval_spin, 4, 1)
        control_layout.addWidget(avg_label, 5, 0)
        control_layout.addWidget(self.avg_spin, 5, 1)
        control_layout.addWidget(self.filter_checkbox, 6, 0, 1, 3)

        control_panel.setLayout(control_layout)
        layout.addWidget(control_panel)

        # ---- Live numeric displays ----
        value_display_layout = QtWidgets.QVBoxLayout()

        # Instantaneous current & moving stats
        current_display_layout = QtWidgets.QHBoxLayout()
        self.value_display = QtWidgets.QLabel("Current: ---")
        self.value_display.setStyleSheet("font-size: 24px; font-weight: bold; color: #2E86C1;")
        self.value_display.setAlignment(QtCore.Qt.AlignCenter)
        current_display_layout.addWidget(self.value_display)

        self.avg_display = QtWidgets.QLabel("Avg: --- nA (σ: ---)")
        self.avg_display.setStyleSheet("font-size: 20px; color: #2E86C1;")
        self.avg_display.setAlignment(QtCore.Qt.AlignCenter)
        current_display_layout.addWidget(self.avg_display)
        value_display_layout.addLayout(current_display_layout)

        # Integrated charge and sputter-current (from OPC UA)
        charge_display_layout = QtWidgets.QHBoxLayout()
        self.charge_display = QtWidgets.QLabel("Charge: 0.00 nC")
        self.charge_display.setStyleSheet("font-size: 24px; font-weight: bold; color: #27AE60;")
        self.charge_display.setAlignment(QtCore.Qt.AlignCenter)
        charge_display_layout.addWidget(self.charge_display)

        self.sputter_display = QtWidgets.QLabel("Sputter: --- mA")
        self.sputter_display.setStyleSheet("font-size: 24px; font-weight: bold; color: #8E44AD;")
        self.sputter_display.setAlignment(QtCore.Qt.AlignCenter)
        charge_display_layout.addWidget(self.sputter_display)
        value_display_layout.addLayout(charge_display_layout)

        layout.addLayout(value_display_layout)

        # ---- Plot ----
        self.figure = Figure(figsize=(10, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        layout.addWidget(self.canvas)

        main_tab.setLayout(layout)
        self.tabs.addTab(main_tab, "Main")

    def update_window_size(self):
        # Sync internal window size with UI control
        self.window_size = self.avg_spin.value()

    def calculate_moving_stats(self):
        # Compute mean/std over last N samples; returns (avg, sigma) or (None, None)
        if len(self.measurements_nA) == 0:
            return None, None
        n = min(self.window_size, len(self.measurements_nA))
        last_n = self.measurements_nA[-n:]
        avg = np.mean(last_n)
        sigma = np.std(last_n)
        return avg, sigma

    def create_gauge_tab(self):
        # Gauge tab: select measurement scale and show a big analog gauge
        gauge_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        # Range buttons (pA/nA/µA scales)
        range_group = QtWidgets.QGroupBox("Current Range")
        range_layout = QtWidgets.QGridLayout()
        self.range_buttons = []
        for i, (min_val, max_val, unit) in enumerate(self.ranges):
            btn = QtWidgets.QPushButton(f"{max_val} {unit}")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, idx=i: self.set_range(idx))
            self.range_buttons.append(btn)
            range_layout.addWidget(btn, i // 3, i % 3)
        self.range_buttons[self.current_range_index].setChecked(True)
        range_group.setLayout(range_layout)
        layout.addWidget(range_group)

        # Gauge widget
        self.gauge = GaugeWidget()
        self.gauge.set_range(0, 100, "nA")
        gauge_layout = QtWidgets.QVBoxLayout()
        gauge_layout.addWidget(self.gauge, 0, QtCore.Qt.AlignCenter)
        layout.addLayout(gauge_layout)

        gauge_tab.setLayout(layout)
        self.tabs.addTab(gauge_tab, "Gauge")

    def clear_graph(self):
        # Reset the visible time origin while continuing acquisition
        if self.is_measuring and len(self.timestamps) > 0:
            self.plot_start_time = self.timestamps[-1]
            self.update_plot()

    def set_range(self, index):
        # Change gauge scale and update button states
        self.current_range_index = index
        min_val, max_val, unit = self.ranges[index]
        for i, btn in enumerate(self.range_buttons):
            btn.setChecked(i == index)
        self.gauge.set_range(min_val, max_val, unit)
        if self.measurements_nA:
            self.update_gauge(self.measurements_nA[-1])

    def update_gauge(self, current_nA):
        # Convert nA to the active unit and clamp to displayable range
        min_val, max_val, unit = self.ranges[self.current_range_index]
        if unit == "pA":
            display_value = current_nA * 1000
        elif unit == "nA":
            display_value = current_nA
        elif unit == "µA":
            display_value = current_nA / 1000
        else:
            display_value = current_nA
        clamped_value = max(min_val, min(max_val, display_value))
        self.gauge.set_value(clamped_value)

    def select_log_file(self):
        # Ask for a CSV destination; only the path is stored, file is opened on start
        options = QtWidgets.QFileDialog.Options()
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Select Log File", "", "Text Files (*.txt);;All Files (*)", options=options)
        if file_path:
            self.log_file = file_path
            self.file_label.setText(os.path.basename(file_path))

    def toggle_measurement(self):
        # Start/stop the acquisition loop and handle instrument connections
        if not self.is_measuring:
            # ---- START ----
            if not self.log_file:
                QtWidgets.QMessageBox.warning(self, "Warning", "Please select a log file first")
                return
            try:
                # Keithley TCP connect + basic init
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect((self.HOST, self.PORT))
                self.initialize_keithley()

                # OPC UA sputter current (best-effort)
                try:
                    self.opc_client = Client(self.opc_url)
                    self.opc_client.connect()
                    self.opc_node = self.opc_client.get_node(self.opc_node_id)
                except Exception as opc_error:
                    print(f"OPC UA connection failed: {opc_error}")

                # Reset buffers and timing
                self.timestamps = []
                self.measurements_nA = []
                self.charge_nC = 0.0
                self.start_time = time.time()
                self.plot_start_time = 0

                # Write header
                with open(self.log_file, 'w') as f:
                    f.write("Timestamp,Elapsed Time (s),Current (nA),Charge (nC),Sputter Current (mA)\n")

                # UI + timer
                self.is_measuring = True
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(True)
                self.clear_button.setEnabled(True)
                self.timer.start(int(self.interval_spin.value() * 1000))
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Connection failed: {str(e)}")
                if self.sock:
                    self.sock.close()
                if self.opc_client:
                    self.opc_client.disconnect()
        else:
            # ---- STOP ----
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
        # SCPI setup for the 6485: reset, zero check off, zero corr on, data format, autorange, NPLC
        init_commands = [
            "*RST", ":SYST:ZCH OFF", ":SYST:ZCOR ON",
            ":FORM:ELEM READ", ":SENS:CURR:RANG:AUTO ON",
            ":SENS:CURR:NPLC 1"
        ]
        for cmd in init_commands:
            self.send_command(cmd, 0.5)

    def send_command(self, command, wait_time=0.1):
        # Send SCPI command, optionally read one reply line if it is a query (ends with '?')
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
        # Poll OPC UA for sputter-current; best-effort read that updates a large label
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
        # One acquisition tick:
        # - READ? current (A), convert to nA
        # - optional filter
        # - integrate charge (nC) via trapezoidal rule
        # - compute moving stats, update UI, append to CSV, refresh plot & gauge
        try:
            response = self.send_command("READ?", 0.5)
            if not response:
                return

            try:
                if '\n' in response:
                    response = response.split('\n')[0]

                current_A = float(response.replace(',', '.'))
                current_nA = abs(current_A) * 1e9

                # Simple spike filter (disabled by default)
                if self.filter_checkbox.isChecked() and current_nA > self.filter_threshold:
                    print(f"Filtered out value: {current_nA:.2f} nA (above threshold)")
                    return

                elapsed = time.time() - self.start_time
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                # Trapezoid area: ((I_prev + I_now)/2) * dt ; since values are in nA and dt in s, result is nC
                if len(self.timestamps) > 0:
                    dt = elapsed - self.timestamps[-1]
                    self.charge_nC += (self.measurements_nA[-1] + current_nA) / 2 * dt

                # Append and compute rolling stats
                self.timestamps.append(elapsed)
                self.measurements_nA.append(current_nA)
                avg, sigma = self.calculate_moving_stats()

                # UI updates
                self.value_display.setText(f"Current: {current_nA:.2f} nA")
                if avg is not None and sigma is not None:
                    self.avg_display.setText(f"Avg: {avg:.2f} nA (σ: {sigma:.2f})")
                self.charge_display.setText(f"Charge: {self.charge_nC:.2f} nC")
                self.update_gauge(current_nA)
                self.update_plot()

                # Append CSV line immediately (flush by reopening)
                with open(self.log_file, 'a') as f:
                    f.write(f"{timestamp},{elapsed:.3f},{current_nA:.2f},{self.charge_nC:.2f},{self.last_sputter_current:.2f}\n")

            except ValueError as e:
                print(f"Value conversion error: {e}")
        except Exception as e:
            # Fail safe: stop the timer, revert UI, and notify user
            print(f"Measurement error: {e}")
            self.timer.stop()
            self.is_measuring = False
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.clear_button.setEnabled(False)
            QtWidgets.QMessageBox.critical(self, "Error", f"Measurement failed: {str(e)}")

    def update_plot(self):
        # Redraw line plot for data after the last clear; auto-scale axes
        self.ax.clear()
        if len(self.timestamps) > 0:
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
        # Cleanly stop acquisition and close all connections on exit
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
