from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import queue
import time

from RtpPacket import RtpPacket
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT
	
	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	
	# Initiation..
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
		self.frameQueue = queue.Queue()
		self.BUFFER_THRESHOLD = 20
		self.init_buffer = True
		self.currentFrameData = b""
		self.updateButtonStates()

		self.stats_time = []
		self.stats_speed = []
		self.total_bytes_interval = 0
		self.last_update_time = time.time()
		self.start_time = time.time()
		self.accumulated_time = 0
		self.stats_window = None # Cửa sổ vẽ biểu đồ
		self.stats_after_id = None  # ID cho biểu đồ
		self.buffer_after_id = None # ID cho playBuffer

		self.timer_is_running = False
		self.elapsedTime = -1
		self.timer_after_id = None
		
	def createWidgets(self):
		"""Build GUI."""
		# Create Setup button
		self.setup = Button(self.master, width=15, padx=3, pady=3)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=2, column=0, padx=2, pady=2)
		
		# Create Play button		
		self.start = Button(self.master, width=15, padx=3, pady=3)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=2, column=1, padx=2, pady=2)
		
		# Create Pause button			
		self.pause = Button(self.master, width=15, padx=3, pady=3)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=2, column=2, padx=2, pady=2)
		
		# Create Teardown button
		self.teardown = Button(self.master, width=15, padx=3, pady=3)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] =  self.exitClient
		self.teardown.grid(row=2, column=3, padx=2, pady=2)

		self.stats = Button(self.master, width=15, padx=3, pady=3)
		self.stats["text"] = "Speed Graph"
		self.stats["command"] = self.showSpeedGraph
		self.stats.grid(row=2, column=4, padx=2, pady=2)
		
		# Create a label to display the movie
		self.label = Label(self.master)
		self.label.grid(row=0, column=0, columnspan=5, sticky=W+E+N+S, padx=5, pady=5)
		MIN_HEIGHT_PIXELS = 300
		self.master.grid_rowconfigure(0, minsize=MIN_HEIGHT_PIXELS, weight=1)

		# Timer
		self.timeLabel = Label(self.master, text="00:00")
		self.timeLabel.grid(row=1, column=0, columnspan=5, sticky=S, pady=10)

	def updateButtonStates(self):
		"""Cập nhật trạng thái các nút dựa trên trạng thái RTSP hiện tại."""
		if not self.master.winfo_exists():
			return

		if self.state == self.INIT:
			# only SETUP
			self.setup.config(state=NORMAL)
			self.start.config(state=DISABLED)
			self.pause.config(state=DISABLED)
			self.teardown.config(state=DISABLED)
		elif self.state == self.READY:
			# PLAY and TEARDOWN
			self.setup.config(state=DISABLED)
			self.start.config(state=NORMAL)
			self.pause.config(state=DISABLED)
			self.teardown.config(state=NORMAL)
		elif self.state == self.PLAYING:
			# PAUSE and TEARDOWN
			self.setup.config(state=DISABLED)
			self.start.config(state=DISABLED)
			self.pause.config(state=NORMAL)
			self.teardown.config(state=NORMAL)

	def updateTimer(self):
		"""Cập nhật bộ đếm thời gian mỗi giây."""
		if self.timer_is_running:
			self.elapsedTime += 1
			mins = self.elapsedTime // 60
			secs = self.elapsedTime % 60
			timeStr = f"{mins:02d}:{secs:02d}"
			self.timeLabel.config(text=timeStr)

			# Luôn đăng ký sau 1 giây để kiểm tra lại trạng thái
			self.timer_after_id = self.master.after(1000, self.updateTimer)
	
	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		"""Teardown button handler."""
		self.sendRtspRequest(self.TEARDOWN)

		if self.timer_after_id:
			self.master.after_cancel(self.timer_after_id)
			self.timer_after_id = None
		self.elapsedTime = 0

		if hasattr(self, 'buffer_after_id') and self.buffer_after_id:
			self.master.after_cancel(self.buffer_after_id)
			self.buffer_after_id = None
		self.closeStatsWindow()

		self.master.destroy() # Close the gui window
		os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video

	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)

			if self.timer_is_running:
				self.timer_is_running = False
				if self.timer_after_id is not None:
					self.master.after_cancel(self.timer_after_id)
					self.timer_after_id = None
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			# Create a new thread to listen for RTP packets
			threading.Thread(target=self.listenRtp).start()
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)

			if self.timer_after_id is None:
				self.timer_is_running = True
				self.updateTimer()
	
	def listenRtp(self):		
		"""Listen for RTP packets."""
		totalBytes = 0
		frameLoss = 0
		startTime = time.time()

		self.total_bytes_interval = 0
		self.start_time = time.time()
		self.last_update_time = self.start_time

		while True:
			try:
				data = self.rtpSocket.recv(20480)
				if data:

					self.total_bytes_interval += len(data)
					now = time.time()
					
					if now - self.last_update_time >= 0.5:
						elapsed = self.accumulated_time + (now - self.start_time)
						speed = (self.total_bytes_interval / (now - self.last_update_time)) / 1024 # KB/s
						
						self.stats_time.append(elapsed)
						self.stats_speed.append(speed)
						
						# Giới hạn dữ liệu hiển thị trong 30 điểm gần nhất
						if len(self.stats_time) > 30:
							self.stats_time.pop(0)
							self.stats_speed.pop(0)
							
						self.total_bytes_interval = 0
						self.last_update_time = now

					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					totalBytes += len(data)
					self.currentFrameData += rtpPacket.getPayload()
					if rtpPacket.getMarker() == 1:
						currFrameNbr = rtpPacket.seqNum()
						print("Received frame: ", currFrameNbr)
						if currFrameNbr > self.frameNbr: # Discard the late packet
							frameLoss += currFrameNbr - self.frameNbr - 1
							self.frameNbr = currFrameNbr
							##self.updateMovie(self.writeFrame(rtpPacket.getPayload()))
							self.frameQueue.put(self.currentFrameData)
							
						self.currentFrameData = b""
					else:
						print(f"  + Reassembling Frame {rtpPacket.seqNum()}")
			except:
				self.accumulated_time += (time.time() - self.start_time)

				if self.state == self.INIT:
					break

				# Stop listening upon requesting PAUSE or TEARDOWN
				if self.playEvent.is_set():
					break
				
				# Upon receiving ACK for TEARDOWN request,
				# close the RTP socket
				if self.teardownAcked == 1:
					self.rtpSocket.shutdown(socket.SHUT_RDWR)
					self.rtpSocket.close()
					break
		endTime = time.time()
		print("Frame Loss: ", frameLoss)
		print("Total bytes: ", totalBytes, " bytes")
		if endTime - startTime > 0:
			print(f"Speed: {(totalBytes / (endTime - startTime)):.2f} bytes/s")

	def showSpeedGraph(self):
		"""Tạo cửa sổ mới để hiển thị biểu đồ thời gian thực."""
		if not self.stats_time:
			tkMessageBox.showinfo("No Data", "Chưa có dữ liệu. Vui lòng nhấn Play trước.")
			return
		if self.stats_window is not None and self.stats_window.winfo_exists():
			self.stats_window.lift() # Đưa cửa sổ lên trên nếu đã tồn tại
			return

		# Tạo cửa sổ phụ
		self.stats_window = Toplevel(self.master)
		self.stats_window.title("Real-time Transmission Stats")
		self.stats_window.configure(bg='black')
		
		# Thiết lập Figure của Matplotlib
		self.fig, self.ax = plt.subplots(figsize=(5, 4), dpi=100)
		self.fig.patch.set_facecolor('black') # Màu nền ngoài biểu đồ
		self.ax.set_facecolor('black')        # Màu nền trong biểu đồ
		
		self.line, = self.ax.plot([], [], color='lime', linewidth=2)
		self.ax.set_title("Network Speed (KB/s)", color='white')
		self.ax.set_xlabel("Time (s)", color='white')
		self.ax.set_ylabel("Speed (KB/s)", color='white')

		self.ax.tick_params(axis='x', colors='white')
		self.ax.tick_params(axis='y', colors='white')

		for spine in self.ax.spines.values():
			spine.set_edgecolor('white')
		self.ax.grid(True, color='gray', linestyle='--', alpha=0.5)

		self.canvas = FigureCanvasTkAgg(self.fig, master=self.stats_window)
		self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
		self.canvas.get_tk_widget().configure(bg='black')

		self.update_stats_plot()

	def update_stats_plot(self):
		"""Hàm cập nhật biểu đồ định kỳ."""
		if self.state == self.INIT or self.stats_window is None or not self.stats_window.winfo_exists():
			return

		if self.stats_time:
			# Cập nhật dữ liệu cho đường vẽ
			self.line.set_data(self.stats_time, self.stats_speed)
			
			# Tự động căn chỉnh trục
			self.ax.set_xlim(min(self.stats_time), max(self.stats_time))
			self.ax.set_ylim(0, max(self.stats_speed) * 1.2 if self.stats_speed else 100)
			self.canvas.draw()

		# Gọi lại sau 500ms
		self.stats_after_id = self.stats_window.after(500, self.update_stats_plot)

	def playBuffer(self):
		if self.state == self.PLAYING:
			if not self.frameQueue.empty():
				if self.init_buffer == True and self.frameQueue.qsize() < self.BUFFER_THRESHOLD:
					print("Pre-buffering, frame:  " + str(self.frameQueue.qsize()))	
				else:
					data = self.frameQueue.get()
					self.updateMovie(self.writeFrame(data))
					self.init_buffer = False
				self.timer_is_running = True
			else:
				print("Buffer is empty!, please wait for data form Server")
				self.init_buffer = True
				self.timer_is_running = False
			self.buffer_after_id = self.master.after(40, self.playBuffer)
		else: 
			return

	def writeFrame(self, data):
		"""Write the received frame to a temp image file. Return the image file."""
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		file = open(cachename, "wb")
		file.write(data)
		file.close()
		
		return cachename
	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		
		image = Image.open(imageFile)
		orig_w, orig_h = image.size
		MAX_W = 1280 
		MAX_H = 720

		if orig_w > MAX_W or orig_h > MAX_H:
			ratio = min(MAX_W/orig_w, MAX_H/orig_h)
			new_w = int(orig_w * ratio)
			new_h = int(orig_h * ratio)
			
			image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
		else:
			pass

		photo = ImageTk.PhotoImage(image)
		self.label.configure(image = photo) 
		self.label.image = photo
		
	def connectToServer(self):
		"""Connect to the Server. Start a new RTSP/TCP session."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""	

		# Setup request
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()
			# Update RTSP sequence number.
			self.rtspSeq += 1
			# Keep track of the sent request.
			self.requestSent = self.SETUP
			# Write the RTSP request to be sent.
			request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
		
		# Play request
		elif requestCode == self.PLAY and self.state == self.READY:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			# Keep track of the sent request.
			self.requestSent = self.PLAY
			# Write the RTSP request to be sent.
			request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
		
		# Pause request
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			# Keep track of the sent request.
			self.requestSent = self.PAUSE
			# Write the RTSP request to be sent.
			request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			
		# Teardown request
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			# Keep track of the sent request.
			self.requestSent = self.TEARDOWN
			# Write the RTSP request to be sent.
			request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
		else:
			return
		
		# Send the RTSP request using rtspSocket.
		self.rtspSocket.send(request.encode())
		
		print('\nData sent:\n' + request)
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			
			if reply: 
				self.parseRtspReply(reply.decode("utf-8"))
			
			# Close the RTSP socket upon requesting Teardown
			if self.requestSent == self.TEARDOWN:
				self.rtspSocket.shutdown(socket.SHUT_RDWR)
				self.rtspSocket.close()
				break
	
	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		
		# Process only if the server reply's sequence number is the same as the request's
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			# New RTSP session ID
			if self.sessionId == 0:
				self.sessionId = session
			
			# Process only if the session ID is the same
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200: 
					if self.requestSent == self.SETUP:
						# Update RTSP state.
						self.state = self.READY
						self.updateButtonStates()
						# Open RTP port.
						self.openRtpPort()

					elif self.requestSent == self.PLAY:
						# Update RTSP state.
						self.state = self.PLAYING
						self.updateButtonStates()
						
						self.playBuffer()

					elif self.requestSent == self.PAUSE:
						# Update RTSP state.
						self.state = self.READY
						self.updateButtonStates()
						# The play thread exits. A new thread is created on resume.
						self.playEvent.set()

					elif self.requestSent == self.TEARDOWN:
						# Update RTSP state.
						self.state = self.INIT
						# Flag the teardownAcked to close the socket.
						self.teardownAcked = 1 
	

	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		try:
			# T?o UDP socket
			self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

			# Set timeout cho socket (0.5 gi�y)
			self.rtpSocket.settimeout(0.5)

			# Bind socket v�o ??a ch? IP v� port (0.0.0.0 ?? l?ng nghe m?i interface)
			self.rtpSocket.bind(("0.0.0.0", self.rtpPort))
			self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 5*1024*1024)

			print(f"RTP socket opened on port {self.rtpPort}")

		except Exception as e:
			tkMessageBox.showwarning('Unable to Bind', f'Unable to bind PORT={self.rtpPort}\nError: {e}')


	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		# self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: # When the user presses cancel, resume playing.
			self.playMovie()

	def closeStatsWindow(self):
		if hasattr(self, 'stats_after_id') and self.stats_after_id:
			try:
				self.stats_window.after_cancel(self.stats_after_id)
			except Exception:
				pass
			self.stats_after_id = None

		# 2. Hủy canvas
		if hasattr(self, 'canvas'):
			self.canvas.get_tk_widget().destroy()
			self.canvas = None

		# 3. Đóng Matplotlib figure (RẤT QUAN TRỌNG)
		if hasattr(self, 'fig'):
			plt.close(self.fig)
			self.fig = None

		# 4. Đóng cửa sổ Tkinter
		if self.stats_window:
			self.stats_window.destroy()
			self.stats_window = None