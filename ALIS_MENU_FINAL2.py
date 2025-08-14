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
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.control = None  # Will be set to reference the parent control

    def wheelEvent(self, event):
        if not self.control:
            return

        delta = event.angleDelta().y()
        step_selector = self.control['step_selector']
        multiplier = self.control['multiplier']

        step_val = step_selector.currentData()
        if step_val is None:
            # Fallback, falls kein userData (sollte aber vorhanden sein)
            step_text = step_selector.currentText().replace(",", ".")
            step_val = float(step_text)

        ticks = max(1, round(step_val * multiplier))
        new_val = self.value() + (ticks if delta > 0 else -ticks)
        self.setValue(min(self.maximum(), max(self.minimum(), new_val)))
        event.accept()

class OPCControlPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OPC UA Control Panel")
        self.setGeometry(100, 100, 800, 800)  # Further reduced window height
        
        # OPC UA connection
        self.client = None
        self.url = "opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2"
        
        # Delta voltage for Einzellinse
        self.delta_voltage = 0.0
        
        # Common step sizes
        self.allowed_steps = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]
        
        # Logging setup
        self.logging_active = False
        self.log_file = None
        
        self.init_ui()
        self.connect_opc()
        
        # Setup auto-refresh timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_all)
        self.refresh_timer.start(1000)  # 1 second interval

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)  # Reduced spacing between groups
        central_widget.setLayout(main_layout)

        # Boolean Controls Section - two columns
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
        bool_layout.setSpacing(15)  # Space between columns
        
        # Create two vertical layouts for the columns
        left_column = QVBoxLayout()
        left_column.setSpacing(3)
        right_column = QVBoxLayout()
        right_column.setSpacing(3)
        
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
        
        # Add controls to left column
        for node_id, description in self.controls[:4]:  # First 4 controls in left column
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
        
        # Add controls to right column
        for node_id, description in self.controls[4:]:  # Remaining controls in right column
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
        
        # Add columns to the main bool layout
        bool_layout.addLayout(left_column)
        bool_layout.addLayout(right_column)
        
        bool_group.setLayout(bool_layout)
        main_layout.addWidget(bool_group)

        # Temperature Control Section
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
        temp_layout.setVerticalSpacing(2)  # Very tight vertical spacing
        temp_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        
        # Current Control (finer: 0.01 A)
        self.current_control = self.create_slider_control(
            0, 2, 100, "A", default_step=0.01, decimals=2
        )
        self.current_control['node_id'] = "ns=3;s=OPC_1.PLC_HV/Analog_Out/Out_Cal_Ofen"
        self.current_control['slider'].valueChanged.connect(self.on_current_changed)
        temp_layout.addRow("Oven Current [A]:", self.current_control['container'])
        
        # Temperature Readout
        self.temp_display = QLabel("--")
        self.temp_display.setAlignment(Qt.AlignLeft)
        self.temp_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Ofen_Temp"
        temp_layout.addRow("Current Temperature [°C]:", self.temp_display)
        
        temp_group.setLayout(temp_layout)
        main_layout.addWidget(temp_group)

        # Ion Source Control Section
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
        source_layout.setVerticalSpacing(1)  # Extremely tight vertical spacing
        source_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        
        # Sputter Voltage
        self.sputter_voltage_control = self.create_slider_control(0, 10000, 10, "V", default_step=10.0)
        self.sputter_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_HV/Analog_Out/Out_Cal_Sputter_U"
        self.sputter_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        source_layout.addRow("Sputter Voltage Control [V]:", self.sputter_voltage_control['container'])
        
        self.sputter_voltage_display = QLabel("--")
        self.sputter_voltage_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Sputter_U"
        source_layout.addRow("Sputter Voltage Indicator [V]:", self.sputter_voltage_display)
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(1)
        source_layout.addRow(separator)
        
        self.sputter_current_display = QLabel("--")
        self.sputter_current_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Sputter_I"
        source_layout.addRow("Sputter Current Indicator [mA]:", self.sputter_current_display)
        
        # Ionizer Current
        self.ionizer_current_display = QLabel("--")
        self.ionizer_current_display.node_id = "ns=3;s=OPC_1.PLC_HV/Analog_In/In_Cal_Ionisierer"
        source_layout.addRow("Ionizer Current Indicator [A]:", self.ionizer_current_display)
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(1)
        source_layout.addRow(separator)
        
        # Extraction Voltage
        self.extraction_voltage_control = self.create_slider_control(0, 30000, 10, "V", default_step=10.0)
        self.extraction_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Extraktion"
        self.extraction_voltage_control['slider'].valueChanged.connect(self.on_extraction_voltage_changed)
        source_layout.addRow("Extraction Voltage Control [V]:", self.extraction_voltage_control['container'])
        
        self.extraction_voltage_display = QLabel("--")
        self.extraction_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Extraktion"
        source_layout.addRow("Extraction Voltage Indicator [V]:", self.extraction_voltage_display)
        
        # Delta Voltage Indicator (changed from control to display)
        self.delta_display = QLabel("--")
        source_layout.addRow("Delta Voltage [V]:", self.delta_display)
        
        # Einzellinse Voltage
        self.einzellinse_voltage_control = self.create_slider_control(0, 30000, 10, "V", default_step=10.0)
        self.einzellinse_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Einzellinse"
        self.einzellinse_voltage_control['slider'].valueChanged.connect(self.on_einzellinse_voltage_changed)
        source_layout.addRow("Einzellinse Voltage Control [V]:", self.einzellinse_voltage_control['container'])
        
        self.einzellinse_voltage_display = QLabel("--")
        self.einzellinse_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Einzellinse"
        source_layout.addRow("Einzellinse Voltage Indicator [V]:", self.einzellinse_voltage_display)
        
        source_group.setLayout(source_layout)
        main_layout.addWidget(source_group)

        # Ion Optics Control Section
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
        optics_layout.setVerticalSpacing(1)  # Extremely tight vertical spacing
        optics_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        
        # Lens 2
        self.lens2_voltage_control = self.create_slider_control(0, 12500, 10, "V", default_step=10.0)
        self.lens2_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Linse2"
        self.lens2_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Lens 2 Voltage Control [V]:", self.lens2_voltage_control['container'])
        
        self.lens2_voltage_display = QLabel("--")
        self.lens2_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Linse2"
        optics_layout.addRow("Lens 2 Voltage Indicator [V]:", self.lens2_voltage_display)
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(1)
        optics_layout.addRow(separator)
        
        # Ion Cooler
        self.ion_cooler_voltage_control = self.create_slider_control(0, 40000, 10, "V", default_step=10.0)
        self.ion_cooler_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_Ionenkuehler"
        self.ion_cooler_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("Ion Cooler Voltage Control [V]:", self.ion_cooler_voltage_control['container'])
        
        self.ion_cooler_voltage_display = QLabel("--")
        self.ion_cooler_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_Ionenkuehler"
        optics_layout.addRow("Ion Cooler Voltage Indicator [V]:", self.ion_cooler_voltage_display)
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(1)
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
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(1)
        optics_layout.addRow(separator)
        
        # ESA
        self.esa_voltage_control = self.create_slider_control(0, 3000, 10, "V", default_step=10.0)
        self.esa_voltage_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_ESA"
        self.esa_voltage_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("ESA Voltage Control [V]:", self.esa_voltage_control['container'])
        
        self.esa_voltage_display = QLabel("--")
        self.esa_voltage_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_ESA"
        optics_layout.addRow("ESA Voltage Indicator [V]:", self.esa_voltage_display)
        
        # ESA Correction
        self.esa_correction_control = self.create_slider_control(0, 1000, 10, "V", default_step=10.0)
        self.esa_correction_control['node_id'] = "ns=3;s=OPC_1.PLC_GND1/Analog_Out/Out_Cal_ESA_Z"
        self.esa_correction_control['slider'].valueChanged.connect(self.on_voltage_changed)
        optics_layout.addRow("ESA Voltage Correction Control [V]:", self.esa_correction_control['container'])
        
        self.esa_correction_display = QLabel("--")
        self.esa_correction_display.node_id = "ns=3;s=OPC_1.PLC_GND1/Analog_In/In_Cal_ESA_Z"
        optics_layout.addRow("ESA Voltage Correction Indicator [V]:", self.esa_correction_display)
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(1)
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

        # Status and Controls
        self.status_label = QLabel("Status: Not connected")
        main_layout.addWidget(self.status_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)
        self.refresh_btn = QPushButton("Refresh Now")
        self.refresh_btn.clicked.connect(self.refresh_all)
        
        self.connect_btn = QPushButton("Reconnect")
        self.connect_btn.clicked.connect(self.reconnect_opc)
        
        self.log_btn = QPushButton("Start Logging")
        self.log_btn.clicked.connect(self.toggle_logging)
        self.log_btn.setCheckable(True)
        
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.log_btn)
        main_layout.addLayout(btn_layout)

    def create_slider_control(self, min_val, max_val, multiplier, unit, default_step=1.0, decimals=1):
        """Create a slider control with independent step selector"""
        container = QWidget()
        layout = QHBoxLayout()
        layout.setSpacing(3)  # Reduced spacing between slider components
        container.setLayout(layout)
        
        # Decrease button
        decrease_btn = QPushButton("◀")
        decrease_btn.setFixedWidth(25)  # Smaller button
        
        # Slider
        slider = ScrollableSlider()
        slider.setMinimum(round(min_val * multiplier))
        slider.setMaximum(round(max_val * multiplier))
        slider.setFixedWidth(180)  # Slightly narrower slider
        slider.setSingleStep(1)    # Keyboard/wheel = 1 tick
        
        # Increase button
        increase_btn = QPushButton("▶")
        increase_btn.setFixedWidth(25)  # Smaller button
        
        # Step size selector
        step_selector = QComboBox()
        step_selector.setFixedWidth(60)  # bit wider for "0.01"
        for step in self.allowed_steps:
            # Display nice (keine erzwungene Rundung), echte Zahl als userData
            step_selector.addItem(format(step, "g"), step)
        
        # Set default step size
        default_index = self.allowed_steps.index(default_step) if default_step in self.allowed_steps else 1
        step_selector.setCurrentIndex(default_index)
        
        # Value display
        value_label = QLabel(f"{min_val:.{decimals}f} {unit}")
        value_label.setStyleSheet("font-weight: bold;")
        value_label.setFixedWidth(90)  # etwas breiter
        value_label.setAlignment(Qt.AlignRight)
        
        # Connect slider to control
        slider.control = {
            'step_selector': step_selector,
            'multiplier': multiplier
        }

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
        
        # Connect signals
        slider.valueChanged.connect(update_value)
        decrease_btn.clicked.connect(decrease_value)
        increase_btn.clicked.connect(increase_value)
        
        # Add widgets to layout
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
        self.connect_opc()

    def toggle_logging(self):
        if self.log_btn.isChecked():
            # Ask user for file path
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Select Log File", "", "Text Files (*.txt);;All Files (*)"
            )
            
            if not file_path:
                self.log_btn.setChecked(False)
                return
                
            try:
                # Start logging to text file
                self.log_file = open(file_path, "w")
                
                # Write header
                header = "Timestamp\t"
                # Add all digital controls to header
                for node_id, description in self.controls:
                    header += f"{description}\t"
                
                # Add all analog indicators
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
            # Stop logging
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
        if not self.logging_active or not self.client or not self.log_file:
            return
            
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_line = f"{timestamp}\t"
            
            # Read all digital values
            for node_id, _ in self.controls:
                node = self.client.get_node(node_id)
                value = node.get_value()
                log_line += f"{value}\t"
            
            # Read all analog values
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
            checkbox.blockSignals(True)
            checkbox.setChecked(not new_value)
            checkbox.blockSignals(False)

    def on_current_changed(self, value):
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
        slider = self.sender()
        control = None
        
        # Find which control triggered this
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
        if not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
            
        try:
            # Set extraction voltage
            node = self.client.get_node(self.extraction_voltage_control['node_id'])
            real_value = float(value) / float(self.extraction_voltage_control['multiplier'])
            node.set_value(real_value, VariantType.Float)
            
            # Update Einzellinse voltage based on new extraction voltage
            self.update_einzellinse_voltage()
        except Exception as e:
            self.status_label.setText(f"Error setting extraction voltage: {str(e)}")

    def on_einzellinse_voltage_changed(self, value):
        if not self.client:
            self.status_label.setText("Status: Not connected - can't set value")
            return
            
        try:
            node = self.client.get_node(self.einzellinse_voltage_control['node_id'])
            real_value = float(value) / float(self.einzellinse_voltage_control['multiplier'])
            node.set_value(real_value, VariantType.Float)
            
            # Update delta display
            extraction_value = self.extraction_voltage_control['slider'].value()
            extraction_voltage = extraction_value / self.extraction_voltage_control['multiplier']
            self.delta_voltage = real_value - extraction_voltage
            self.delta_display.setText(f"{self.delta_voltage:.1f} V")
        except Exception as e:
            self.status_label.setText(f"Error setting Einzellinse voltage: {str(e)}")

    def update_einzellinse_voltage(self):
        if not self.client:
            return
            
        try:
            # Calculate new Einzellinse voltage
            extraction_value = self.extraction_voltage_control['slider'].value()
            extraction_voltage = extraction_value / self.extraction_voltage_control['multiplier']
            new_einzellinse_voltage = extraction_voltage + self.delta_voltage
            
            # Update control slider (without triggering another change)
            self.einzellinse_voltage_control['slider'].blockSignals(True)
            slider_value = round(new_einzellinse_voltage * self.einzellinse_voltage_control['multiplier'])
            self.einzellinse_voltage_control['slider'].setValue(slider_value)
            self.einzellinse_voltage_control['slider'].blockSignals(False)
            
            # Send to OPC
            node = self.client.get_node(self.einzellinse_voltage_control['node_id'])
            node.set_value(new_einzellinse_voltage, VariantType.Float)
            
            # Update delta display
            self.delta_display.setText(f"{self.delta_voltage:.1f} V")
        except Exception as e:
            self.status_label.setText(f"Error updating Einzellinse voltage: {str(e)}")

    def refresh_all(self):
        if not self.client:
            self.status_label.setText("Status: Not connected - can't read values")
            return
            
        try:
            # Refresh boolean values
            for node_id, checkbox in self.checkboxes.items():
                node = self.client.get_node(node_id)
                value = node.get_value()
                checkbox.blockSignals(True)
                checkbox.setChecked(value)
                checkbox.blockSignals(False)
            
            # Refresh oven controls
            current_node = self.client.get_node(self.current_control['node_id'])
            current_value = current_node.get_value()
            s = self.current_control['slider']
            if not s.isSliderDown():
                s.blockSignals(True)
                s.setValue(round(current_value * self.current_control['multiplier']))
                s.blockSignals(False)
            
            temp_node = self.client.get_node(self.temp_display.node_id)
            temp_value = temp_node.get_value()
            self.temp_display.setText(f"{temp_value:.1f} °C")
            
            # Refresh source controls and indicators
            self.refresh_voltage(self.sputter_voltage_control)
            self.refresh_voltage_display("sputter_voltage_display", "V")
            self.refresh_voltage_display("sputter_current_display", "mA", 3)
            self.refresh_voltage_display("ionizer_current_display", "A")
            
            self.refresh_voltage(self.extraction_voltage_control)
            self.refresh_voltage_display("extraction_voltage_display", "V")
            
            self.refresh_voltage(self.einzellinse_voltage_control)
            self.refresh_voltage_display("einzellinse_voltage_display", "V")
            
            # Calculate and display delta voltage
            extraction_value = self.extraction_voltage_control['slider'].value()
            extraction_voltage = extraction_value / self.extraction_voltage_control['multiplier']
            einzellinse_value = self.einzellinse_voltage_control['slider'].value()
            einzellinse_voltage = einzellinse_value / self.einzellinse_voltage_control['multiplier']
            self.delta_voltage = einzellinse_voltage - extraction_voltage
            self.delta_display.setText(f"{self.delta_voltage:.1f} V")
            
            # Refresh ion optics controls and indicators
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
            
            # Write log entry if logging is active
            if self.logging_active:
                self.write_log_entry()
                
        except Exception as e:
            self.status_label.setText(f"Error reading values: {str(e)}")

    def refresh_voltage(self, control):
        node = self.client.get_node(control['node_id'])
        value = node.get_value()
        slider = control['slider']
        if slider.isSliderDown():
            return
        slider.blockSignals(True)
        slider.setValue(round(value * control['multiplier']))
        slider.blockSignals(False)

    def refresh_voltage_display(self, display_name, unit, decimals=1):
        display = getattr(self, display_name)
        node = self.client.get_node(display.node_id)
        value = node.get_value()
        display.setText(f"{value:.{decimals}f} {unit}")

    def closeEvent(self, event):
        if self.client:
            self.client.disconnect()
        if self.log_file:
            self.log_file.close()
        self.refresh_timer.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OPCControlPanel()
    window.show()
    sys.exit(app.exec_())
