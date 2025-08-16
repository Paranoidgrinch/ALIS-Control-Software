# Stepper Motor Controller UI
# - PyQt5 GUI to move a stepper to named positions and run measurement sequences
# - Talks to a TCP motor controller (ASCII protocol)
# - Loads named positions from file; supports manual offsets
# - Triggers measurements by time or by new data file count
# - Uses a worker QThread for sequences so the UI stays responsive
# - Periodic status polling updates labels; logging shown in a text box

import socket
import time
import glob
from datetime import datetime
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import QThread, pyqtSignal, Qt


class StepperMotorController(QtWidgets.QMainWindow):
    # Main window: builds the UI, manages the TCP connection, and coordinates a SequenceWorker.
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stepper Motor Controller")
        self.setFixedSize(900, 700)

        # --- TCP controller configuration ---
        self.HOST = "192.168.0.6"
        self.PORT = 102
        self.sock = None

        # --- Position data (loaded from file, adjusted in UI) ---
        self.position_file = r"C:\Users\ALIS\Desktop\ALIS_LABVIEW\ALIS_Positionen.txt"
        self.position_map = {}          # name -> base position (int)
        self.position_sequence = []     # list of dicts: {name, position, trigger_mode, trigger_value}
        self.current_adjustment = 0

        # --- File monitoring for "File Count" trigger ---
        self.data_path = r"\\192.168.0.1\current analysis\*.blk"
        self.last_data_count = 0
        self.data_counter = 0

        # Build UI, load positions, and attempt an initial connection.
        self.create_ui()
        self.load_positions()
        self.connect_motor()

    def create_ui(self):
        # Compose the entire UI: connection label, position controls, sequence list,
        # manual command, status labels, and log view. Also start a 1 Hz status timer.
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        # --- Connection Status (updated by connect_motor/connect errors) ---
        self.connection_label = QtWidgets.QLabel("Status: Disconnected")
        self.connection_label.setStyleSheet("font-size: 14px; color: red;")

        # --- Position Controls (select named position, offset, trigger type/value, path) ---
        pos_group = QtWidgets.QGroupBox("Position Control")
        pos_layout = QtWidgets.QGridLayout()

        self.position_combo = QtWidgets.QComboBox()

        # Fine adjustment (manual offset in steps)
        self.adjust_spin = QtWidgets.QSpinBox()
        self.adjust_spin.setRange(-10000, 10000)
        self.adjust_spin.setValue(0)
        self.adjust_spin.setSuffix(" steps")

        # Measurement trigger: time-based or file-count-based
        self.trigger_mode = QtWidgets.QComboBox()
        self.trigger_mode.addItems(["Time (seconds)", "File Count"])
        self.trigger_mode.currentIndexChanged.connect(self.update_trigger_ui)

        self.measure_value_spin = QtWidgets.QSpinBox()
        self.measure_value_spin.setRange(1, 10000)
        self.measure_value_spin.setValue(10)

        # Path for file-count trigger
        self.path_label = QtWidgets.QLabel("Data Path:")
        self.path_edit = QtWidgets.QLineEdit(self.data_path)
        self.path_edit.textChanged.connect(self.update_data_path)

        # Sequence controls
        self.add_to_sequence_btn = QtWidgets.QPushButton("Add to Sequence")
        self.add_to_sequence_btn.clicked.connect(self.add_to_sequence)

        self.clear_sequence_btn = QtWidgets.QPushButton("Clear Sequence")
        self.clear_sequence_btn.clicked.connect(self.clear_sequence)

        self.move_btn = QtWidgets.QPushButton("Move to Position")
        self.move_btn.clicked.connect(self.move_to_position)

        self.run_sequence_btn = QtWidgets.QPushButton("Run Sequence")
        self.run_sequence_btn.clicked.connect(self.run_sequence)

        self.stop_btn = QtWidgets.QPushButton("STOP")
        self.stop_btn.setStyleSheet("background-color: red; color: white;")
        self.stop_btn.clicked.connect(self.stop_movement)

        self.home_btn = QtWidgets.QPushButton("Go Home")
        self.home_btn.clicked.connect(self.go_home)

        # Layout wiring for the Position Control group
        pos_layout.addWidget(QtWidgets.QLabel("Position:"), 0, 0)
        pos_layout.addWidget(self.position_combo, 0, 1)
        pos_layout.addWidget(QtWidgets.QLabel("Manual Adjustment:"), 1, 0)
        pos_layout.addWidget(self.adjust_spin, 1, 1)
        pos_layout.addWidget(QtWidgets.QLabel("Trigger Mode:"), 2, 0)
        pos_layout.addWidget(self.trigger_mode, 2, 1)
        pos_layout.addWidget(QtWidgets.QLabel("Measurement Value:"), 3, 0)
        pos_layout.addWidget(self.measure_value_spin, 3, 1)
        pos_layout.addWidget(self.path_label, 4, 0)
        pos_layout.addWidget(self.path_edit, 4, 1)
        pos_layout.addWidget(self.add_to_sequence_btn, 5, 0)
        pos_layout.addWidget(self.clear_sequence_btn, 5, 1)
        pos_layout.addWidget(self.move_btn, 6, 0)
        pos_layout.addWidget(self.run_sequence_btn, 6, 1)
        pos_layout.addWidget(self.home_btn, 7, 0)
        pos_layout.addWidget(self.stop_btn, 7, 1)
        pos_group.setLayout(pos_layout)

        # --- Sequence Display (read-only list of planned moves) ---
        seq_group = QtWidgets.QGroupBox("Position Sequence")
        seq_layout = QtWidgets.QVBoxLayout()
        self.sequence_list = QtWidgets.QListWidget()
        seq_layout.addWidget(self.sequence_list)
        seq_group.setLayout(seq_layout)

        # --- Manual Command (raw commands to the controller) ---
        cmd_group = QtWidgets.QGroupBox("Manual Command")
        cmd_layout = QtWidgets.QVBoxLayout()
        self.cmd_input = QtWidgets.QLineEdit()
        self.cmd_input.setPlaceholderText("Enter command (e.g., 's r0xca 87709')")
        self.send_btn = QtWidgets.QPushButton("Send Command")
        self.send_btn.clicked.connect(self.send_manual_command)
        cmd_layout.addWidget(self.cmd_input)
        cmd_layout.addWidget(self.send_btn)
        cmd_group.setLayout(cmd_layout)

        # --- Status Display (current pos, what’s next, measurement progress) ---
        status_group = QtWidgets.QGroupBox("Status")
        status_layout = QtWidgets.QVBoxLayout()
        self.current_pos_label = QtWidgets.QLabel("Current Position: -")
        self.next_move_label = QtWidgets.QLabel("Next Move: -")
        self.measurement_status = QtWidgets.QLabel("Measurement Status: -")
        status_layout.addWidget(self.current_pos_label)
        status_layout.addWidget(self.next_move_label)
        status_layout.addWidget(self.measurement_status)
        status_group.setLayout(status_layout)

        # --- Log view (timestamped messages) ---
        self.log_display = QtWidgets.QTextEdit()
        self.log_display.setReadOnly(True)

        # Assemble the main layout
        layout.addWidget(self.connection_label)
        layout.addWidget(pos_group)
        layout.addWidget(seq_group)
        layout.addWidget(status_group)
        layout.addWidget(cmd_group)
        layout.addWidget(self.log_display)
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        # Initialize UI state and start a 1 Hz status poll
        self.update_trigger_ui()
        self.status_timer = QtCore.QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(1000)

    def update_trigger_ui(self):
        # Adjust the suffix on the measurement value depending on trigger mode.
        if self.trigger_mode.currentText() == "Time (seconds)":
            self.measure_value_spin.setSuffix(" s")
        else:
            self.measure_value_spin.setSuffix(" files")

    def update_data_path(self):
        # Keep data_path in sync with the UI field for file-count trigger.
        self.data_path = self.path_edit.text()

    def load_positions(self):
        # Load "name -> position" mappings from a text file and populate the combo box.
        try:
            self.position_combo.clear()
            with open(self.position_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split()
                        if len(parts) >= 2:
                            name = ' '.join(parts[:-1])
                            value = parts[-1]
                            self.position_map[name] = int(value)
                            self.position_combo.addItem(name)
            self.position_combo.insertItem(0, "Select position")
            self.position_combo.setCurrentIndex(0)
            self.log(f"Loaded {len(self.position_map)} positions from file")
        except Exception as e:
            self.log(f"Error loading positions: {str(e)}")

    def connect_motor(self):
        # Establish (or re-establish) the TCP socket to the motor controller.
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(2.0)
            self.sock.connect((self.HOST, self.PORT))
            self.connection_label.setText("Status: Connected")
            self.connection_label.setStyleSheet("color: green;")
            self.log("Connected to motor controller")
        except Exception as e:
            self.connection_label.setText(f"Status: Error ({str(e)})")
            self.log(f"Connection error: {str(e)}")

    def send_command(self, command):
        # Send a single ASCII command and return the single-line response (or None on error).
        try:
            if not self.sock:
                self.connect_motor()
            self.sock.sendall((command + "\n").encode('ascii'))
            response = self.sock.recv(1024).decode('ascii').strip()
            self.log(f"Sent: {command} | Received: {response}")
            return response
        except Exception as e:
            self.log(f"Command error: {str(e)}")
            return None

    def add_to_sequence(self):
        # Append the currently selected position (plus manual offset) with its trigger settings.
        pos_name = self.position_combo.currentText()
        if pos_name in self.position_map:
            base_position = self.position_map[pos_name]
            adjustment = self.adjust_spin.value()
            target_position = base_position + adjustment

            trigger_mode = self.trigger_mode.currentText()
            trigger_value = self.measure_value_spin.value()

            self.position_sequence.append({
                'name': pos_name,
                'position': target_position,
                'trigger_mode': trigger_mode,
                'trigger_value': trigger_value
            })
            self.update_sequence_display()
            self.log(f"Added {pos_name} to sequence (Pos: {target_position}, Trigger: {trigger_mode} {trigger_value})")
        else:
            self.log("Please select a valid position")

    def clear_sequence(self):
        # Remove all planned steps from the sequence.
        self.position_sequence = []
        self.update_sequence_display()
        self.log("Sequence cleared")

    def update_sequence_display(self):
        # Refresh the on-screen list to match position_sequence.
        self.sequence_list.clear()
        for idx, item in enumerate(self.position_sequence, 1):
            self.sequence_list.addItem(
                f"{idx}. {item['name']} (Pos: {item['position']}, {item['trigger_mode']}: {item['trigger_value']})"
            )

    def move_to_position(self):
        # Move immediately to the selected position (including manual offset).
        pos_name = self.position_combo.currentText()
        if pos_name in self.position_map:
            base_position = self.position_map[pos_name]
            adjustment = self.adjust_spin.value()
            target_position = base_position + adjustment

            self.log(f"Moving to position {pos_name} (Pos: {target_position})")

            # Set position, then execute move
            response = self.send_command(f"s r0xca {target_position}")
            if response != "ok":
                self.log("Failed to set position")
                return
            response = self.send_command("t 1")
            if response == "ok":
                self.log("Movement started successfully")
            else:
                self.log("Movement command failed")
        else:
            self.log("Please select a valid position")

    def run_sequence(self):
        # Launch the worker thread to execute the entire sequence (non-blocking).
        if not self.position_sequence:
            self.log("No positions in sequence")
            return

        self.log("Starting position sequence")
        self.sequence_worker = SequenceWorker(
            self,
            self.position_sequence.copy(),
            self.sock,
            self.data_path
        )
        self.sequence_worker.update_status.connect(self.update_sequence_status)
        self.sequence_worker.finished.connect(self.on_sequence_complete)
        self.sequence_worker.start()

    def update_sequence_status(self, current_pos, next_move, status):
        # Update the three status labels while the sequence runs.
        self.current_pos_label.setText(f"Current Position: {current_pos}")
        self.next_move_label.setText(f"Next Move: {next_move}")
        self.measurement_status.setText(f"Measurement Status: {status}")

    def stop_movement(self):
        # Stop the running sequence (if any) and issue a stop command to the controller.
        if hasattr(self, 'sequence_worker') and self.sequence_worker.isRunning():
            self.sequence_worker.stop()
            self.log("Sequence stopped by user")
        self.send_command("t 0")  # Stop motion
        self.log("Movement stopped")

    def go_home(self):
        # Command the controller to return to its home position.
        self.log("Returning to home position")
        response = self.send_command("t 2")
        if response == "ok":
            self.log("Home command accepted")
        else:
            self.log("Home command failed")

    def send_manual_command(self):
        # Send whatever the user typed into the manual command box.
        command = self.cmd_input.text().strip()
        if command:
            response = self.send_command(command)
            if response:
                self.log(f"Command response: {response}")

    def update_status(self):
        # Periodic 1 Hz poll to update the current position label during idle/sequence.
        if self.sock and not hasattr(self, 'sequence_worker'):
            try:
                response = self.send_command("g r0x30")
                if response and response.startswith("v "):
                    self.current_pos_label.setText(f"Current Position: {response[2:]}")
            except:
                pass

    def on_sequence_complete(self, success):
        # Reset status labels and log the outcome once the worker finishes.
        if success:
            self.log("Sequence completed successfully")
        else:
            self.log("Sequence stopped")
        self.update_sequence_status("-", "-", "-")

    def log(self, message, timestamp=True):
        # Append a timestamped line to the log view and auto-scroll to bottom.
        if timestamp:
            message = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.log_display.append(message)
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum())

    def closeEvent(self, event):
        # Graceful shutdown: stop the worker and close the socket.
        if hasattr(self, 'sequence_worker') and self.sequence_worker.isRunning():
            self.sequence_worker.stop()
        if self.sock:
            self.sock.close()
        event.accept()


class SequenceWorker(QThread):
    # Background thread that executes the position sequence, reports progress,
    # and waits for measurement triggers (time or file count).
    update_status = pyqtSignal(str, str, str)
    finished = pyqtSignal(bool)

    def __init__(self, controller, sequence, sock, data_path):
        super().__init__()
        self.controller = controller
        self.sequence = sequence
        self.sock = sock
        self.data_path = data_path
        self._running = True

    def run(self):
        # Iterate over the planned steps:
        # - move to target
        # - wait for motion completion
        # - perform measurement by time or file-appearance count
        try:
            for idx, item in enumerate(self.sequence):
                if not self._running:
                    break

                pos_name = item['name']
                position = item['position']
                trigger_mode = item['trigger_mode']
                trigger_value = item['trigger_value']

                # Next move label (peek ahead)
                next_move = f"{pos_name} ({position})" if idx+1 < len(self.sequence) else "None"
                self.update_status.emit(str(position), next_move, "Moving...")

                # Set target and start motion
                response = self.send_command(f"s r0xca {position}")
                if response != "ok":
                    self.finished.emit(False)
                    return
                response = self.send_command("t 1")
                if response != "ok":
                    self.finished.emit(False)
                    return

                # Wait until current position equals target (max ~20 s)
                start_time = time.time()
                while time.time() - start_time < 20 and self._running:
                    response = self.send_command("g r0x30")
                    if response and response.startswith("v "):
                        if response[2:] == str(position):
                            break
                    time.sleep(1)

                if not self._running:
                    break

                # Measurement phase
                if trigger_mode == "Time (seconds)":
                    # Time-based hold at the current position
                    self.controller.log(f"Measuring at {pos_name} for {trigger_value}s")
                    for remaining in range(trigger_value, 0, -1):
                        if not self._running:
                            break
                        self.update_status.emit(str(position), next_move, f"Measuring... {remaining}s remaining")
                        time.sleep(1)
                else:
                    # File-count-based wait (watch data_path for new files)
                    self.controller.log(f"Measuring at {pos_name} until {trigger_value} new files appear")
                    initial_count = self.get_data_count()
                    self.update_status.emit(str(position), next_move, f"Waiting for {trigger_value} files... (0/{trigger_value})")

                    while self._running:
                        current_count = self.get_data_count()
                        if current_count > initial_count:
                            change = current_count - initial_count
                            self.update_status.emit(
                                str(position),
                                next_move,
                                f"Waiting for {trigger_value} files... ({change}/{trigger_value})"
                            )
                            if change >= trigger_value:
                                break
                        time.sleep(1)

                if not self._running:
                    break

            self.finished.emit(self._running)
        except Exception as e:
            self.controller.log(f"Sequence error: {str(e)}")
            self.finished.emit(False)

    def get_data_count(self):
        # Count files matching data_path for the file-count trigger.
        try:
            data = glob.glob(self.data_path)
            return len(data)
        except Exception as e:
            self.controller.log(f"Error getting data count: {str(e)}")
            return 0

    def send_command(self, command):
        # Send a controller command via the shared socket used by the UI.
        try:
            self.sock.sendall((command + "\n").encode('ascii'))
            return self.sock.recv(1024).decode('ascii').strip()
        except Exception as e:
            self.controller.log(f"Command error: {str(e)}")
            return None

    def stop(self):
        # Cooperative stop flag checked between steps and in waits.
        self._running = False


# --- Entry point -------------------------------------------------------------
if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = StepperMotorController()
    window.show()
    app.exec_()
