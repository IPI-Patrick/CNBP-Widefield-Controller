#!/usr/bin/env python3

import os
import sys
import tempfile
import time


def _resolve_benchmark_directory(dirname):
	drive_root = os.path.splitdrive(os.path.abspath(dirname))[0] + os.sep
	candidate_directories = []
	system_temp = tempfile.gettempdir()
	if os.path.splitdrive(system_temp)[0].lower() == os.path.splitdrive(drive_root)[0].lower():
		candidate_directories.append(system_temp)

	candidate_directories.extend(
		[
			dirname,
			os.path.join(drive_root, "Temp"),
			os.path.join(drive_root, "tmp"),
			os.path.join(drive_root, "Users", "Public"),
			drive_root,
		]
	)

	attempted_directories = []
	for directory in candidate_directories:
		normalized_directory = os.path.abspath(directory)
		if normalized_directory in attempted_directories:
			continue
		attempted_directories.append(normalized_directory)
		if not os.path.isdir(normalized_directory):
			continue
		if os.access(normalized_directory, os.W_OK):
			return normalized_directory

	raise PermissionError(f"No writable benchmark directory found for drive {drive_root}")


def writetofile(filename, mysize_mb):
	# Write bytes repeatedly until the requested size is reached, then delete the file.
	payload = b"The quick brown fox jumps over the lazy dog"
	target_bytes = int(1_000_000 * float(mysize_mb))
	writeloops = max(1, target_bytes // len(payload))

	with open(filename, "wb") as handle:
		for _ in range(writeloops):
			handle.write(payload)

	os.remove(filename)


def diskspeedmeasure(dirname):
	# Return sequential write speed to dirname in MB/s.
	filesize_mb = 1.0
	maxtime_seconds = 0.5
	benchmark_directory = _resolve_benchmark_directory(dirname)
	start = time.perf_counter()
	loopcounter = 0

	while True:
		file_descriptor, filename = tempfile.mkstemp(
			prefix="outputTESTING_",
			suffix=".tmp",
			dir=benchmark_directory,
		)
		os.close(file_descriptor)
		writetofile(filename, filesize_mb)
		loopcounter += 1
		diff = time.perf_counter() - start
		if diff > maxtime_seconds:
			break

	return (loopcounter * filesize_mb) / max(diff, 1e-6)


def measure_write_speed(dirname):
	# CameraControls expects bytes per second.
	return diskspeedmeasure(dirname) * 1_000_000.0


if __name__ == "__main__":
	print("Let's go")

	if len(sys.argv) >= 2:
		dirname = sys.argv[1]
		if not os.path.isdir(dirname):
			print("Specified argument is not a directory. Bailing out")
			sys.exit(1)
	else:
		dirname = os.getcwd()
		print("Using current working directory")

	try:
		speed = diskspeedmeasure(dirname)
		print("Disk writing speed: %.2f Mbytes per second" % speed)
	except IOError as exc:
		if exc.errno == 13:
			print("Could not create test file. Check that you have write rights to directory", dirname)
		else:
			raise

	print("Done")