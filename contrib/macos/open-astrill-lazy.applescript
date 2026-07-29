on run
	set localPort to "16086"
	set remotePort to "6086"
	set sshHost to "glassagent-ubuntu"
	set healthURL to "http://127.0.0.1:" & localPort & "/vnc.html"
	set controllerURL to healthURL & "?autoconnect=1&resize=scale"

	try
		do shell script "/usr/bin/curl --silent --fail --max-time 1 " & quoted form of healthURL & " >/dev/null"
	on error
		try
			do shell script "/usr/bin/ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L " & localPort & ":127.0.0.1:" & remotePort & " " & quoted form of sshHost
		on error messageText
			display alert "Astrill Lazy Router" message "Could not create the secure controller tunnel." & return & return & messageText as critical
			return
		end try
	end try

	repeat 20 times
		try
			do shell script "/usr/bin/curl --silent --fail --max-time 1 " & quoted form of healthURL & " >/dev/null"
			open location controllerURL
			return
		on error
			delay 0.25
		end try
	end repeat

	display alert "Astrill Lazy Router" message "The secure tunnel opened, but the controller did not become ready." as warning
end run
