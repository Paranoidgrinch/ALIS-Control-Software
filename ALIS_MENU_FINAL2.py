# OPC UA Control Panel
# - PyQt5 GUI to monitor and set digital/analog signals for the ion source and optics
# - Communicates with a PLC via OPC UA (python-opcua Client)
# - Groups: digital toggles, oven current/temperature, source voltages, ion optics
# - Each analog control = slider + step-size selector + value readout
# - 1 Hz auto-refresh reads values; optional logging writes a tab-separated file
# - “Delta Voltage” = Einzellinse - Extraction (kept consistent when either changes)

import sys
from datetime import datetime
from opcua import Client
from opcua.ua import VariantType
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QPushButton, QCheckBox,
                            QGroupBox, QFormLayout, QFileDialog, QComboBox,
                            QSlider, QSizePolicy, QFrame)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette


class ScrollableSlider(QSlider):
    # Slider that uses mouse wheel + a per-control step selector to adjust in user-chosen ticks.
    # The parent control dictionary is attached to slider.control by create_slider_control().
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.control = None  # set by create_slider_control

    def wheelEvent(self, event):
        # Wheel delta -> +/- ticks; ticks derive from (step size * multiplier) with min 1 tick.
        if not self.control:
            return

        delta = event.angleDelta().y()
        step_selector = self.control['step_selector']
        multiplier = self.control['multiplier']

        step_val = step_selector.currentData()
        if step_val is None:
            # Fallback if userData missing: parse visible text (supports comma decimal).
            step_text = step_selector.currentText().replace(",", ".")
            step_val = float(step_text)

        ticks = max(1, round(step_val * multiplier))
        new_val = self.value() + (ticks if delta > 0 else -ticks)
        self.setValue(min(self.maximum(), max(self.minimum(), new_val)))
        event.accept()


class OPCControlPanel(QMainWindow):
    # Top-level window: builds grouped controls, manages OPC UA client, refresh/logging, and write-backs.
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OPC UA Control Panel")
        self.setGeometry(100, 100, 800, 800)  # compact window

        # --- OPC UA connection state ---
        self.client = None
        self.url = "opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2"

        # --- Derived parameter: Einzellinse - Extraction (kept when Extraction changes) ---
        self.delta_voltage = 0.0

        # --- Common step sizes for analog sliders (user-selectable) ---
        self.allowed_steps = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]

        # --- Logging toggles/handle ---
        self.logging_active = False
        self.log_file = None

        # Build UI and connect once at startup.
        self.init_ui()
        self.connect_opc()

        # --- Auto-refresh timer (1 Hz) reads all indicators and updates displays ---
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_all)
        self.refresh_timer.start(1000)

    def init_ui(self):
        # Compose all groups: digital controls, oven, source, optics, status + action buttons.
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)
        central_widget.setLayout(main_layout)

        # === Digital (Boolean) Controls: two columns of toggles ===
        bool_group = QGroupBox("Digital Controls")
        bool_group.setStyleSheet("""
            QGroupBox { 
                background-color: #f0f8ff;
                border: 1px solid gray;
                border-radius: 3px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        bool_layout = QHBoxLayout()
        bool_layout.setSpacing(15)

        # Two columns for compactness
        left_column = QVBoxLayout()
        left_column.setSpacing(3)
        right_column = QVBoxLayout()
        right_column.setSpacing(3)

        # NodeIds and labels for digital outputs (mirrored by checkboxes)
        self.controls = [
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Attenuator", "Attenuator"),
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Cup1", "Cup 1"),
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Cup2", "Cup 2"),
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Cup3", "Cup 3"),
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Cup4", "Cup 4"),
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Cup5", "Cup 5"),
            ("ns=3;s=OPC_1.PLC_GND1/Digital_Out/Quick-Cool", "Quick-Cool")
        ]

        self.checkboxes = {}

        # Left column: first 4
        for node_id, description in self.controls[:4]:
            hbox = QHBoxLayout()
            hbox.setSpacing(5)
            label = QLabel(description)
            checkbox = QCheckBox()
            checkbox.node_id = node_id
            checkbox.stateChanged.connect(self.on_checkbox_changed)
            hbox.addWidget(label)
            hbox.addWidget(checkbox)
            left_column.addLayout(hbox)
            self.checkboxes[node_id] = checkbox

        # Right column: remaining
        for node_id, description in self.controls[4:]:
            hbox = QHBoxLayout()
            hbox.setSpacing(5)
            label = QLabel(description)
            checkbox = QCheckBox()
            checkbox.node_id = node_id
            checkbox.stateChanged.connect(self.on_checkbox_changed)
            hbox.addWidget(label)
            hbox.addWidget(checkbox)
            right_column.addLayout(hbox)
            self.checkboxes[node_id] = checkbox

        bool_layout.addLayout(left_column)
        bool_layout.addLayout(right_column)
        bool_group.setLayout(bool_layout)
        main_layout.addWidget(bool_group)

        # === Oven current control + temperature readback ===
        temp_group = QGroupBox("Oven Temperature Control")
        temp_group.setStyleSheet("""
            QGroupBox { 
                background-color: #fff0f5;
                border: 1px solid gray;
                border-radius: 3px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        temp_layout = QFormLayout()
        temp_layout.setVerticalSpacing(2)
        temp_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Oven current control (fine steps down to 0.01 A)
        self.current_control = self.create_slider_control(
            0, 2, 100, "A", default_step=0.01, decimals=2
        )
        self.current_control['node_id'] = "ns=3;s=OPC_1.PLC_HV/Analog_Out/Out_Cal_Ofen"
        self.current_control['slider'].valueChanged.connect(self.on_current_changed)
        temp_layout.addRow("Oven Current [A]:", self.current_control['container'])

        # Temperature readout (indicator only)
        self.temp_display = QLabel("--")
        self.temp_display.setAlignment(Qt.AlignLeft)
        self.temp_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Ofen_Temp"
        temp_layout.addRow("Current Temperature [°C]:", self.temp_display)

        temp_group.setLayout(temp_layout)
        main_layout.addWidget(temp_group)

        # === Source voltages and currents ===
        source_group = QGroupBox("Ion Source Controls")
        source_group.setStyleSheet("""
            QGroupBox { 
                background-color: #f0fff0;
                border: 1px solid gray;
                border-radius: 3px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        source_layout = QFormLayout()
        source_layout.setVerticalSpacing(1)
        source_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Sputter voltage (control + indicator)
        self.sputter_voltage_control = self.create_slider_control(0, 10000, 10, "V", default_step=10.0)
        self.sputter_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_HV/Analog_Out/Out_Cal_Sputter_U"
        self.sputter_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        source_layout.addRow("Sputter Voltage Control [V]:", self.sputter_voltage_control['container'])

        self.sputter_voltage_display = QLabel("--")
        self.sputter_voltage_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Sputter_U"
        source_layout.addRow("Sputter Voltage Indicator [V]:", self.sputter_voltage_display)

        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken); separator.setFixedHeight(1)
        source_layout.addRow(separator)

        self.sputter_current_display = QLabel("--")
        self.sputter_current_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Sputter_I"
        source_layout.addRow("Sputter Current Indicator [mA]:", self.sputter_current_display)

        self.ionizer_current_display = QLabel("--")
        self.ionizer_current_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Ionisierer"
        source_layout.addRow("Ionizer Current Indicator [A]:", self.ionizer_current_display)

        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken); separator.setFixedHeight(1)
        source_layout.addRow(separator)

        # Extraction voltage (control + indicator)
        self.extraction_voltage_control = self.create_slider_control(0, 30000, 10, "V", default_step=10.0)
        self.extraction_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Extraktion"
        self.extraction_voltage_control['slider'].valueChanged.connect(self.on_extraction_voltage_changed)
        source_layout.addRow("Extraction Voltage Control [V]:", self.extraction_voltage_control['container'])

        self.extraction_voltage_display = QLabel("--")
        self.extraction_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Extraktion"
        source_layout.addRow("Extraction Voltage Indicator [V]:", self.extraction_voltage_display)

        # Delta = Einzellinse - Extraction (display only; kept up to date on changes)
        self.delta_display = QLabel("--")
        source_layout.addRow("Delta Voltage [V]:", self.delta_display)

        # Einzellinse voltage (control + indicator)
        self.einzellinse_voltage_control = self.create_slider_control(0, 30000, 10, "V", default_step=10.0)
        self.einzellinse_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Einzellinse"
        self.einzellinse_voltage_control['slider'].valueChanged.connect(self.on_einzellinse_voltage_changed)
        source_layout.addRow("Einzellinse Voltage Control [V]:", self.einzellinse_voltage_control['container'])

        self.einzellinse_voltage_display = QLabel("--")
        self.einzellinse_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Einzellinse"
        source_layout.addRow("Einzellinse Voltage Indicator [V]:", self.einzellinse_voltage_display)

        source_group.setLayout(source_layout)
        main_layout.addWidget(source_group)

        # === Ion optics (multiple lenses/quadrupoles/ESA) ===
        optics_group = QGroupBox("Ion Optics Controls")
        optics_group.setStyleSheet("""
            QGroupBox { 
                background-color: #f5f0ff;
                border: 1px solid gray;
                border-radius: 3px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)
        optics_layout = QFormLayout()
        optics_layout.setVerticalSpacing(1)
        optics_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Lens 2
        self.lens2_voltage_control = self.create_slider_control(0, 12500, 10, "V", default_step=10.0)
        self.lens2_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Linse2"
        self.lens2_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Lens 2 Voltage Control [V]:", self.lens2_voltage_control['container'])
        self.lens2_voltage_display = QLabel("--")
        self.lens2_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Linse2"
        optics_layout.addRow("Lens 2 Voltage Indicator [V]:", self.lens2_voltage_display)

        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken); separator.setFixedHeight(1)
        optics_layout.addRow(separator)

        # Ion Cooler
        self.ion_cooler_voltage_control = self.create_slider_control(0, 40000, 10, "V", default_step=10.0)
        self.ion_cooler_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Ionenkuehler"
        self.ion_cooler_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Ion Cooler Voltage Control [V]:", self.ion_cooler_voltage_control['container'])
        self.ion_cooler_voltage_display = QLabel("--")
        self.ion_cooler_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Ionenkuehler"
        optics_layout.addRow("Ion Cooler Voltage Indicator [V]:", self.ion_cooler_voltage_display)

        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken); separator.setFixedHeight(1)
        optics_layout.addRow(separator)

        # Quadrupole 1
        self.quad1_voltage_control = self.create_slider_control(0, 6000, 10, "V", default_step=10.0)
        self.quad1_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Quad1"
        self.quad1_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Quadrupole 1 Voltage Control [V]:", self.quad1_voltage_control['container'])
        self.quad1_voltage_display = QLabel("--")
        self.quad1_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Quad1"
        optics_layout.addRow("Quadrupole 1 Voltage Indicator [V]:", self.quad1_voltage_display)

        # Quadrupole 2
        self.quad2_voltage_control = self.create_slider_control(0, 6000, 10, "V", default_step=10.0)
        self.quad2_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Quad2"
        self.quad2_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Quadrupole 2 Voltage Control [V]:", self.quad2_voltage_control['container'])
        self.quad2_voltage_display = QLabel("--")
        self.quad2_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Quad2"
        optics_layout.addRow("Quadrupole 2 Voltage Indicator [V]:", self.quad2_voltage_display)

        # Quadrupole 3
        self.quad3_voltage_control = self.create_slider_control(0, 6000, 10, "V", default_step=10.0)
        self.quad3_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Quad3"
        self.quad3_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Quadrupole 3 Voltage Control [V]:", self.quad3_voltage_control['container'])
        self.quad3_voltage_display = QLabel("--")
        self.quad3_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Quad3"
        optics_layout.addRow("Quadrupole 3 Voltage Indicator [V]:", self.quad3_voltage_display)

        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken); separator.setFixedHeight(1)
        optics_layout.addRow(separator)

        # ESA + correction
        self.esa_voltage_control = self.create_slider_control(0, 3000, 10, "V", default_step=10.0)
        self.esa_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_ESA"
        self.esa_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("ESA Voltage Control [V]:", self.esa_voltage_control['container'])
        self.esa_voltage_display = QLabel("--")
        self.esa_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_ESA"
        optics_layout.addRow("ESA Voltage Indicator [V]:", self.esa_voltage_display)

        self.esa_correction_control = self.create_slider_control(0, 1000, 10, "V", default_step=10.0)
        self.esa_correction_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_ESA_Z"
        self.esa_correction_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("ESA Voltage Correction Control [V]:", self.esa_correction_control['container'])
        self.esa_correction_display = QLabel("--")
        self.esa_correction_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_ESA_Z"
        optics_layout.addRow("ESA Voltage Correction Indicator [V]:", self.esa_correction_display)

        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken); separator.setFixedHeight(1)
        optics_layout.addRow(separator)

        # Lens 4
        self.lens4_voltage_control = self.create_slider_control(0, 10000, 10, "V", default_step=10.0)
        self.lens4_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Linse4"
        self.lens4_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Lens 4 Voltage Control [V]:", self.lens4_voltage_control['container'])
        self.lens4_voltage_display = QLabel("--")
        self.lens4_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Linse4"
        optics_layout.addRow("Lens 4 Voltage Indicator [V]:", self.lens4_voltage_display)

        optics_group.setLayout(optics_layout)
        main_layout.addWidget(optics_group)

        # === Global status banner ===
        self.status_label = QLabel("Status: Not connected")
        main_layout.addWidget(self.status_label)

        # === Action buttons ===
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)
        self.refresh_btn = QPushButton("Refresh Now"); self.refresh_btn.clicked.connect(self.refresh_all)
        self.connect_btn = QPushButton("Reconnect"); self.connect_btn.clicked.connect(self.reconnect_opc)
        self.log_btn = QPushButton("Start Logging"); self.log_btn.clicked.connect(self.toggle_logging); self.log_btn.setCheckable(True)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.log_btn)
        main_layout.addLayout(btn_layout)

    def create_slider_control(self, min_val, max_val, multiplier, unit, default_step=1.0, decimals=1):
        # Build a reusable analog control row:
        # [◀] [slider scaled by 'multiplier'] [▶] [step selector] [bold value label with unit]
        # multiplier maps real units -> integer slider ticks (e.g., 10 ticks per volt).
        container = QWidget()
        layout = QHBoxLayout()
        layout.setSpacing(3)
        container.setLayout(layout)

        # Decrease button
        decrease_btn = QPushButton("◀")
        decrease_btn.setFixedWidth(25)

        # Slider (integer ticks)
        slider = ScrollableSlider()
        slider.setMinimum(round(min_val * multiplier))
        slider.setMaximum(round(max_val * multiplier))
        slider.setFixedWidth(180)
        slider.setSingleStep(1)  # keyboard/wheel default tick (actual step chosen via combo)

        # Increase button
        increase_btn = QPushButton("▶")
        increase_btn.setFixedWidth(25)

        # Step size selector (user chooses delta per wheel/click)
        step_selector = QComboBox()
        step_selector.setFixedWidth(60)
        for step in self.allowed_steps:
            step_selector.addItem(format(step, "g"), step)  # show text, keep numeric userData

        # Default step
        default_index = self.allowed_steps.index(default_step) if default_step in self.allowed_steps else 1
        step_selector.setCurrentIndex(default_index)

        # Value display (bold, right-aligned)
        value_label = QLabel(f"{min_val:.{decimals}f} {unit}")
        value_label.setStyleSheet("font-weight: bold;")
        value_label.setFixedWidth(90)
        value_label.setAlignment(Qt.AlignRight)

        # Attach control metadata so ScrollableSlider can read step size and scaling.
        slider.control = {
            'step_selector': step_selector,
            'multiplier': multiplier
        }

        # Local helpers to keep the UI in sync and apply stepped changes.
        def update_value(value):
            real_value = value / multiplier
            value_label.setText(f"{real_value:.{decimals}f} {unit}")

        def step_ticks():
            val = step_selector.currentData()
            if val is None:
                val = float(step_selector.currentText().replace(",", "."))
            return max(1, round(val * multiplier))

        def decrease_value():
            slider.setValue(max(slider.minimum(), slider.value() - step_ticks()))

        def increase_value():
            slider.setValue(min(slider.maximum(), slider.value() + step_ticks()))

        # Wire events
        slider.valueChanged.connect(update_value)
        decrease_btn.clicked.connect(decrease_value)
        increase_btn.clicked.connect(increase_value)

        # Pack row
        layout.addWidget(decrease_btn)
        layout.addWidget(slider)
        layout.addWidget(increase_btn)
        layout.addWidget(step_selector)
        layout.addWidget(value_label)

        return {
            'container': container,
            'slider': slider,
            'decrease_btn': decrease_btn,
            'increase_btn': increase_btn,
            'step_selector': step_selector,
            'value_label': value_label,
            'multiplier': multiplier
        }

    def connect_opc(self):
        # (Re)connect to the OPC UA server, update banner, and do an immediate refresh.
        try:
            if self.client:
                self.client.disconnect()
            self.client = Client(self.url)
            self.client.connect()
            self.status_label.setText("Status: Connected")
            self.refresh_all()
        except Exception as e:
            self.status_label.setText(f"Status: Connection failed - {str(e)}")

    def reconnect_opc(self):
        # Manual reconnect trigger (calls connect_opc).
        self.connect_opc()

    def toggle_logging(self):
        # Start/stop TSV logging of all digital/analog indicators (1 line per refresh).
        if self.log_btn.isChecked():
            # Choose output file path
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Select Log File", "", "Text Files (*.txt);;All Files (*)"
            )
            if not file_path:
                self.log_btn.setChecked(False)
                return
            try:
                # Open file and write header once
                self.log_file = open(file_path, "w")
                header = "Timestamp\t"
                # Digital controls
                for node_id, description in self.controls:
                    header += f"{description}\t"
                # Analog indicator names (order matches write_log_entry)
                indicators = [
                    ("Oven Temp", self.temp_display),
                    ("Sputter V", self.sputter_voltage_display),
                    ("Sputter I", self.sputter_current_display),
                    ("Ionizer I", self.ionizer_current_display),
                    ("Extract V", self.extraction_voltage_display),
                    ("Einzellinse V", self.einzellinse_voltage_display),
                    ("Lens2 V", self.lens2_voltage_display),
                    ("Ion Cooler V", self.ion_cooler_voltage_display),
                    ("Quad1 V", self.quad1_voltage_display),
                    ("Quad2 V", self.quad2_voltage_display),
                    ("Quad3 V", self.quad3_voltage_display),
                    ("ESA V", self.esa_voltage_display),
                    ("ESA Corr V", self.esa_correction_display),
                    ("Lens4 V", self.lens4_voltage_display)
                ]
                for name, _ in indicators:
                    header += f"{name}\t"
                self.log_file.write(header.rstrip() + "\n")
                self.logging_active = True
                self.log_btn.setText("Stop Logging")
                self.status_label.setText(f"Status: Logging to {file_path}")
            except Exception as e:
                self.log_btn.setChecked(False)
                self.status_label.setText(f"Error opening log file: {str(e)}")
        else:
            # Stop logging and close file.
            if self.log_file:
                try:
                    self.log_file.close()
                except:
                    pass
                self.log_file = None
            self.logging_active = False
            self.log_btn.setText("Start Logging")
            self.status_label.setText("Status: Logging stopped")

    def write_log_entry(self):
        # Append one TSV line with timestamp + all digital states + all analog indicators.
        if not self.logging_active or not self.client or not self.log_file:
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_line = f"{timestamp}\t"

            # Digital values (checkboxes mirror them)
            for node_id, _ in self.controls:
                node = self.client.get_node(node_id)
                value = node.get_value()
                log_line += f"{value}\t"

            # Analog values (same order as header)
            indicators = [
                self.temp_display, self.sputter_voltage_display,
                self.sputter_current_display, self.ionizer_current_display,
                self.extraction_voltage_display, self.einzellinse_voltage_display,
                self.lens2_voltage_display, self.ion_cooler_voltage_display,
                self.quad1_voltage_display, self.quad2_voltage_display,
                self.quad3_voltage_display, self.esa_voltage_display,
                self.esa_correction_display, self.lens4_voltage_display
            ]
            for indicator in indicators:
                node = self.client.get_node(indicator.node_id)
                value = node.get_value()
                log_line += f"{value:.3f}\t"

            self.log_file.write(log_line.rstrip() + "\n")
            self.log_file.flush()
        except Exception as e:
            self.status_label.setText(f"Logging error: {str(e)}")

    def on_checkbox_changed(self, state):
        # Write a boolean digital output when a checkbox is toggled.
        checkbox = self.sender()
        if not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
        try:
            node = self.client.get_node(checkbox.node_id)
            new_value = state == Qt.Checked
            node.set_value(new_value)
        except Exception as e:
            self.status_label.setText(f"Error: {str(e)}")
            # revert UI to previous state
            checkbox.blockSignals(True)
            checkbox.setChecked(not new_value)
            checkbox.blockSignals(False)

    def on_current_changed(self, value):
        # Write oven current (float) from slider ticks.
        if not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
        try:
            node = self.client.get_node(self.current_control['node_id'])
            real_value = float(value) / float(self.current_control['multiplier'])
            node.set_value(real_value, VariantType.Float)
        except Exception as e:
            self.status_label.setText(f"Error setting current: {str(e)}")

    def on_voltage_changed(self, value):
        # Generic handler for many voltage sliders (find which control fired, then write).
        slider = self.sender()
        control = None
        for c in [self.sputter_voltage_control, self.lens2_voltage_control, 
                  self.ion_cooler_voltage_control, self.quad1_voltage_control,
                  self.quad2_voltage_control, self.quad3_voltage_control,
                  self.esa_voltage_control, self.esa_correction_control,
                  self.lens4_voltage_control]:
            if c['slider'] == slider:
                control = c
                break

        if not control or not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
        try:
            node = self.client.get_node(control['node_id'])
            real_value = float(value) / float(control['multiplier'])
            node.set_value(real_value, VariantType.Float)
        except Exception as e:
            self.status_label.setText(f"Error setting voltage: {str(e)}")

    def on_extraction_voltage_changed(self, value):
        # Write extraction voltage, then recompute Einzellinse so (Einzellinse - Extraction) = delta_voltage.
        if not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
        try:
            node = self.client.get_node(self.extraction_voltage_control['node_id'])
            real_value = float(value) / float(self.extraction_voltage_control['multiplier'])
            node.set_value(real_value, VariantType.Float)

            # Keep Einzellinse in sync with new extraction voltage.
            self.update_einzellinse_voltage()
        except Exception as e:
            self.status_label.setText(f"Error setting extraction voltage: {str(e)}")

    def on_einzellinse_voltage_changed(self, value):
        # Write Einzellinse voltage and update delta display.
        if not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
        try:
            node = self.client.get_node(self.einzellinse_voltage_control['node_id'])
            real_value = float(value) / float(self.einzellinse_voltage_control['multiplier'])
            node.set_value(real_value, VariantType.Float)

            # Recompute delta = Einzellinse - Extraction for display.
            extraction_value = self.extraction_voltage_control['slider'].value()
            extraction_voltage = extraction_value / self.extraction_voltage_control['multiplier']
            self.delta_voltage = real_value - extraction_voltage
            self.delta_display.setText(f"{self.delta_voltage:.1f} V")
        except Exception as e:
            self.status_label.setText(f"Error setting Einzellinse voltage: {str(e)}")

    def update_einzellinse_voltage(self):
        # Apply current delta_voltage to the new extraction voltage (keeps spacing constant).
        if not self.client:
            return
        try:
            # Calculate desired Einzellinse = Extraction + delta
            extraction_value = self.extraction_voltage_control['slider'].value()
            extraction_voltage = extraction_value / self.extraction_voltage_control['multiplier']
            new_einzellinse_voltage = extraction_voltage + self.delta_voltage

            # Update slider without recursive signal
            self.einzellinse_voltage_control['slider'].blockSignals(True)
            slider_value = round(new_einzellinse_voltage * self.einzellinse_voltage_control['multiplier'])
            self.einzellinse_voltage_control['slider'].setValue(slider_value)
            self.einzellinse_voltage_control['slider'].blockSignals(False)

            # Write to OPC
            node = self.client.get_node(self.einzellinse_voltage_control['node_id'])
            node.set_value(new_einzellinse_voltage, VariantType.Float)

            # Refresh delta display
            self.delta_display.setText(f"{self.delta_voltage:.1f} V")
        except Exception as e:
            self.status_label.setText(f"Error updating Einzellinse voltage: {str(e)}")

    def refresh_all(self):
        # Read all values and update UI (1 Hz). Also append a log line if logging is active.
        if not self.client:
            self.status_label.setText("Status: Not connected - can't read values")
            return
        try:
            # Booleans: sync checkboxes with PLC values (without emitting writes).
            for node_id, checkbox in self.checkboxes.items():
                node = self.client.get_node(node_id)
                value = node.get_value()
                checkbox.blockSignals(True)
                checkbox.setChecked(value)
                checkbox.blockSignals(False)

            # Oven current slider follows PLC unless user is dragging.
            current_node = self.client.get_node(self.current_control['node_id'])
            current_value = current_node.get_value()
            s = self.current_control['slider']
            if not s.isSliderDown():
                s.blockSignals(True)
                s.setValue(round(current_value * self.current_control['multiplier']))
                s.blockSignals(False)

            # Temperature indicator
            temp_node = self.client.get_node(self.temp_display.node_id)
            temp_value = temp_node.get_value()
            self.temp_display.setText(f"{temp_value:.1f} °C")

            # Source controls/indicators
            self.refresh_voltage(self.sputter_voltage_control)
            self.refresh_voltage_display("sputter_voltage_display", "V")
            self.refresh_voltage_display("sputter_current_display", "mA", 3)
            self.refresh_voltage_display("ionizer_current_display", "A")

            self.refresh_voltage(self.extraction_voltage_control)
            self.refresh_voltage_display("extraction_voltage_display", "V")

            self.refresh_voltage(self.einzellinse_voltage_control)
            self.refresh_voltage_display("einzellinse_voltage_display", "V")

            # Delta update from current sliders
            extraction_value = self.extraction_voltage_control['slider'].value()
            extraction_voltage = extraction_value / self.extraction_voltage_control['multiplier']
            einzellinse_value = self.einzellinse_voltage_control['slider'].value()
            einzellinse_voltage = einzellinse_value / self.einzellinse_voltage_control['multiplier']
            self.delta_voltage = einzellinse_voltage - extraction_voltage
            self.delta_display.setText(f"{self.delta_voltage:.1f} V")

            # Ion optics controls/indicators
            self.refresh_voltage(self.lens2_voltage_control)
            self.refresh_voltage_display("lens2_voltage_display", "V")

            self.refresh_voltage(self.ion_cooler_voltage_control)
            self.refresh_voltage_display("ion_cooler_voltage_display", "V")

            self.refresh_voltage(self.quad1_voltage_control)
            self.refresh_voltage_display("quad1_voltage_display", "V")

            self.refresh_voltage(self.quad2_voltage_control)
            self.refresh_voltage_display("quad2_voltage_display", "V")

            self.refresh_voltage(self.quad3_voltage_control)
            self.refresh_voltage_display("quad3_voltage_display", "V")

            self.refresh_voltage(self.esa_voltage_control)
            self.refresh_voltage_display("esa_voltage_display", "V")

            self.refresh_voltage(self.esa_correction_control)
            self.refresh_voltage_display("esa_correction_display", "V")

            self.refresh_voltage(self.lens4_voltage_control)
            self.refresh_voltage_display("lens4_voltage_display", "V")

            self.status_label.setText("Status: Auto-refreshing")

            # Write one logging line if active
            if self.logging_active:
                self.write_log_entry()

        except Exception as e:
            self.status_label.setText(f"Error reading values: {str(e)}")

    def refresh_voltage(self, control):
        # Sync one analog control slider with PLC (unless user is dragging).
        node = self.client.get_node(control['node_id'])
        value = node.get_value()
        slider = control['slider']
        if slider.isSliderDown():
            return
        slider.blockSignals(True)
        slider.setValue(round(value * control['multiplier']))
        slider.blockSignals(False)

    def refresh_voltage_display(self, display_name, unit, decimals=1):
        # Update a single indicator label from its node (display only).
        display = getattr(self, display_name)
        node = self.client.get_node(display.node_id)
        value = node.get_value()
        display.setText(f"{value:.{decimals}f} {unit}")

    def closeEvent(self, event):
        # Clean shutdown: disconnect OPC, close log file, stop timer.
        if self.client:
            self.client.disconnect()
        if self.log_file:
            self.log_file.close()
        self.refresh_timer.stop()
        event.accept()


# --- Entry point -------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OPCControlPanel()
    window.show()
    sys.exit(app.exec_())
