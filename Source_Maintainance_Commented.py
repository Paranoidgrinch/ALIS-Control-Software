# -*- coding: utf-8 -*-
"""
ionizer_maintenance_control_annotated.py

A PyQt5-based graphical interface for controlling and monitoring
an ion source maintenance system via an OPC UA server.

Features:
- Real-time status indicators for key system components.
- Manual control buttons for actuating valves, pumps, and sample wheel.
- Guided maintenance procedures via step-by-step dialogs.
- OPC UA client integration for reading and writing Boolean nodes.
- Error handling and reconnection logic.

Author: Generated with ChatGPT
"""

import sys  # Provides access to system-specific parameters and functions
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QMessageBox, QDialog,
    QFormLayout
)
from PyQt5.QtCore import Qt, QTimer  # Qt provides core non-GUI functionality; QTimer for periodic callbacks
from PyQt5.QtGui import QColor, QPalette  # For coloring state indicators
from opcua import Client, ua  # OPC UA client library for industrial communication


class StateIndicator(QLabel):
    """
    A simple colored square that reflects a Boolean state.

    Green indicates True (active), Red indicates False (inactive).
    Inherits from QLabel and uses its background color.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set a fixed size for consistency
        self.setFixedSize(20, 20)
        # Initialize to False (red)
        self.set_state(False)
    
    def set_state(self, state):
        """
        Update the indicator color based on the state.

        :param state: Boolean, True for green, False for red.
        """
        palette = self.palette()
        # Choose green or red based on state
        color = QColor(0, 255, 0) if state else QColor(255, 0, 0)
        palette.setColor(QPalette.Window, color)
        self.setAutoFillBackground(True)
        self.setPalette(palette)
        self.update()  # Trigger a repaint


class MaintenanceGuide(QDialog):
    """
    A modal dialog that walks the user through a maintenance procedure step-by-step.

    Each step is shown in sequence, with a "Next" button. On the final step,
    the button changes to "Finish" and closes the dialog.
    """
    def __init__(self, steps, title, parent=None):
        super().__init__(parent)
        # Set dialog properties
        self.setWindowTitle(title)
        self.setModal(True)
        
        # Vertical layout for the content
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Label to display the current step text
        self.step_label = QLabel()
        self.step_label.setWordWrap(True)
        layout.addWidget(self.step_label)
        
        # Track current step index and steps list
        self.current_step = 0
        self.steps = steps
        
        # Button to advance to next step or finish
        self.next_button = QPushButton("Next Step")
        self.next_button.clicked.connect(self.next_step)
        layout.addWidget(self.next_button)
        
        # Show the first step immediately
        self.update_step()
    
    def update_step(self):
        """
        Display the current step in the label and adjust the button text
        if this is the last step.
        """
        if self.current_step < len(self.steps):
            # Number steps starting at 1 for user readability
            step_text = self.steps[self.current_step]
            self.step_label.setText(f"Step {self.current_step + 1}: {step_text}")
            # Change button text on final step
            if self.current_step == len(self.steps) - 1:
                self.next_button.setText("Finish")
    
    def next_step(self):
        """
        Advance to the next step; close dialog when all steps shown.
        """
        self.current_step += 1
        if self.current_step >= len(self.steps):
            self.accept()  # Close the dialog with success result
        else:
            self.update_step()


class IonizerMaintenanceControl(QMainWindow):
    """
    Main application window for ion source maintenance control.

    Sets up UI sections for:'
    - Maintenance guide dialogs
    - Live state indicators
    - Manual control buttons

    Manages an OPC UA Client to communicate with the hardware.
    """
    def __init__(self):
        super().__init__()
        # Configure main window
        self.setWindowTitle("Ion Source Maintenance Control")
        self.setGeometry(100, 100, 800, 600)
        
        # Map logical names to OPC UA node strings
        self.nodes = {
            'wheel': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Wheel",
            'source_valve': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Valve_Source",
            'vent': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Vent",
            'pump_valve': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Valve_Pump",
            'pump': "ns=3;s=OPC_1.PLC_HV/Digital_Out/Pump"
        }
        
        # OPC UA client placeholder (connected on demand)
        self.client = None
        # URL of the OPC UA server
        self.server_url = "opc.tcp://DESKTOP-UH9J072:4980/Softing_dataFEED_OPC_Suite_Configuration2"
        
        # Build the GUI
        self.init_ui()
        
        # Timer for periodic state updates (every 1 second)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_states)
        self.monitor_timer.start(1000)
    
    def init_ui(self):
        """
        Construct all UI elements and layout:
        - Maintenance guide buttons
        - State indicator group
        - Manual control buttons for each actuator
        """
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # ---- Maintenance Guide Section ----
        guide_group = QGroupBox("Maintenance Guides")
        guide_layout = QHBoxLayout()
        
        self.open_guide_btn = QPushButton("Open Source Guide")
        self.open_guide_btn.clicked.connect(self.show_open_guide)
        
        self.close_guide_btn = QPushButton("Close Source Guide")
        self.close_guide_btn.clicked.connect(self.show_close_guide)
        
        guide_layout.addWidget(self.open_guide_btn)
        guide_layout.addWidget(self.close_guide_btn)
        guide_group.setLayout(guide_layout)
        main_layout.addWidget(guide_group)
        
        # ---- Live State Indicators ----
        state_group = QGroupBox("Source States")
        state_layout = QFormLayout()
        
        # Create each StateIndicator and add it with a label
        self.wheel_indicator = StateIndicator()
        state_layout.addRow("Sample Wheel Retracted:", self.wheel_indicator)
        
        self.source_valve_indicator = StateIndicator()
        state_layout.addRow("Source Valve Closed:", self.source_valve_indicator)
        
        self.vent_indicator = StateIndicator()
        state_layout.addRow("Venting Active:", self.vent_indicator)
        
        self.pump_valve_indicator = StateIndicator()
        state_layout.addRow("Pump Valve Open:", self.pump_valve_indicator)
        
        self.pump_indicator = StateIndicator()
        state_layout.addRow("Pumping Active:", self.pump_indicator)
        
        state_group.setLayout(state_layout)
        main_layout.addWidget(state_group)
        
        # ---- Manual Controls ----
        control_group = QGroupBox("Manual Controls")
        control_layout = QVBoxLayout()
        
        # Sample Wheel Controls
        wheel_group = QGroupBox("Sample Wheel")
        wheel_layout = QHBoxLayout()
        self.retract_wheel_btn = QPushButton("Retract Sample Wheel")
        self.retract_wheel_btn.clicked.connect(lambda: self.set_node_state('wheel', True))
        self.drive_in_wheel_btn = QPushButton("Drive In Sample Wheel")
        self.drive_in_wheel_btn.clicked.connect(lambda: self.set_node_state('wheel', False))
        wheel_layout.addWidget(self.retract_wheel_btn)
        wheel_layout.addWidget(self.drive_in_wheel_btn)
        wheel_group.setLayout(wheel_layout)
        control_layout.addWidget(wheel_group)
        
        # Source Valve Controls
        valve_group = QGroupBox("Source Valve")
        valve_layout = QHBoxLayout()
        self.close_source_valve_btn = QPushButton("Close Source Valve")
        self.close_source_valve_btn.clicked.connect(lambda: self.set_node_state('source_valve', True))
        self.open_source_valve_btn = QPushButton("Open Source Valve")
        self.open_source_valve_btn.clicked.connect(lambda: self.set_node_state('source_valve', False))
        valve_layout.addWidget(self.close_source_valve_btn)
        valve_layout.addWidget(self.open_source_valve_btn)
        valve_group.setLayout(valve_layout)
        control_layout.addWidget(valve_group)
        
        # Argon Venting Controls
        vent_group = QGroupBox("Argon Venting")
        vent_layout = QHBoxLayout()
        self.start_vent_btn = QPushButton("Start Argon Venting")
        self.start_vent_btn.clicked.connect(self.confirm_start_venting)
        self.stop_vent_btn = QPushButton("Stop Argon Venting")
        self.stop_vent_btn.clicked.connect(lambda: self.set_node_state('vent', False))
        vent_layout.addWidget(self.start_vent_btn)
        vent_layout.addWidget(self.stop_vent_btn)
        vent_group.setLayout(vent_layout)
        control_layout.addWidget(vent_group)
        
        # Pump Valve Controls
        pump_valve_group = QGroupBox("Pump Valve")
        pump_valve_layout = QHBoxLayout()
        self.open_pump_valve_btn = QPushButton("Open Pump Valve")
        self.open_pump_valve_btn.clicked.connect(self.confirm_open_pump_valve)
        self.close_pump_valve_btn = QPushButton("Close Pump Valve")
        self.close_pump_valve_btn.clicked.connect(lambda: self.set_node_state('pump_valve', False))
        pump_valve_layout.addWidget(self.open_pump_valve_btn)
        pump_valve_layout.addWidget(self.close_pump_valve_btn)
        pump_valve_group.setLayout(pump_valve_layout)
        control_layout.addWidget(pump_valve_group)
        
        # Pump Controls
        pump_group = QGroupBox("Pump")
        pump_layout = QHBoxLayout()
        self.start_pump_btn = QPushButton("Start Pumping")
        self.start_pump_btn.clicked.connect(lambda: self.set_node_state('pump', True))
        self.stop_pump_btn = QPushButton("Stop Pumping")
        self.stop_pump_btn.clicked.connect(lambda: self.set_node_state('pump', False))
        pump_layout.addWidget(self.start_pump_btn)
        pump_layout.addWidget(self.stop_pump_btn)
        pump_group.setLayout(pump_layout)
        control_layout.addWidget(pump_group)
        
        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)
    
    def show_open_guide(self):
        """
        Display the sequence of steps for safely opening the source.
        """
        steps = [
            "Press Sample Wheel Retract",
            # ... additional detailed instructions
            "Done!"
        ]
        guide = MaintenanceGuide(steps, "Opening Source Guide", self)
        guide.exec_()  # Blocks until the user finishes or cancels
    
    def show_close_guide(self):
        """
        Display the sequence of steps for safely closing the source.
        """
        steps = [
            "Put the Wheel back in",
            # ... additional detailed instructions
            "Done!"
        ]
        guide = MaintenanceGuide(steps, "Closing Source Guide", self)
        guide.exec_()
    
    def connect_opc(self):
        """
        (Re)connect to the OPC UA server. Disconnects existing client if present.

        :returns: True if connection established, False otherwise.
        """
        try:
            # If a client exists, try to disconnect cleanly
            if self.client:
                try:
                    self.client.disconnect()
                except:
                    pass
            # Create and connect a new client
            self.client = Client(self.server_url)
            self.client.connect()
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            self.client = None
            return False
    
    def update_states(self):
        """
        Periodic callback to read all OPC UA node values and update UI indicators.
        If the connection is lost, attempt to reconnect; otherwise, show all red.
        """
        if not self.client:
            if not self.connect_opc():
                # Show fault state if cannot connect
                self.set_all_indicators(False)
                return
        try:
            # Read each Boolean node
            values = {}
            for name, node_id in self.nodes.items():
                values[name] = self.client.get_node(node_id).get_value()
            # Reflect the values in the UI
            self.wheel_indicator.set_state(values['wheel'])
            self.source_valve_indicator.set_state(values['source_valve'])
            self.vent_indicator.set_state(values['vent'])
            self.pump_valve_indicator.set_state(values['pump_valve'])
            self.pump_indicator.set_state(values['pump'])
        except Exception as e:
            print(f"Error updating states: {e}")
            # On error, drop connection and reset UI
            try:
                if self.client:
                    self.client.disconnect()
            except:
                pass
            self.client = None
            self.set_all_indicators(False)
    
    def set_all_indicators(self, state):
        """
        Force all state indicators to the same Boolean value.

        Useful for showing fault or disconnected state.
        """
        for indicator in [
            self.wheel_indicator,
            self.source_valve_indicator,
            self.vent_indicator,
            self.pump_valve_indicator,
            self.pump_indicator
        ]:
            indicator.set_state(state)
    
    def set_node_state(self, node_name, state):
        """
        Write a Boolean value to the specified OPC UA node.

        :param node_name: Key from self.nodes mapping.
        :param state: True/False for the desired state.
        :returns: True if successful, False on error.
        """
        # Ensure we have an active connection
        if not self.client:
            if not self.connect_opc():
                QMessageBox.critical(self, "Error", "Could not connect to OPC server")
                return False
        try:
            node = self.client.get_node(self.nodes[node_name])
            # Wrap Python bool in an OPC UA Variant Boolean
            variant = ua.Variant(state, ua.VariantType.Boolean)
            node.set_value(variant)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not set {node_name} state: {e}")
            # On failure, disconnect to reset
            try:
                if self.client:
                    self.client.disconnect()
            except:
                pass
            self.client = None
            return False
    
    def confirm_start_venting(self):
        """
        Prompt the user before starting argon venting, as this action
        introduces gas into the system.
        """
        reply = QMessageBox.question(
            self, 'Confirm Venting',
            "Are you sure you want to start argon venting?\n\n"
            "This will release gas into the system.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.set_node_state('vent', True)
    
    def confirm_open_pump_valve(self):
        """
        Prompt the user before opening the pump valve,
        as this will connect the pump to the system.
        """
        reply = QMessageBox.question(
            self, 'Confirm Pump Valve',
            "Are you sure you want to open the pump valve?\n\n"
            "This will connect the pump to the system.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.set_node_state('pump_valve', True)
    
    def closeEvent(self, event):
        """
        Override the window close event to cleanly disconnect the OPC UA client
        before exiting the application.
        """
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
        event.accept()


if __name__ == "__main__":
    # Create the Qt application and main window, then start the event loop
    app = QApplication(sys.argv)
    window = IonizerMaintenanceControl()
    window.show()
    sys.exit(app.exec_())
