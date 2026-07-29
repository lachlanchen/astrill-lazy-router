on endpointReady(healthURL)
	try
		do shell script "/usr/bin/curl --silent --fail --max-time 1 " & quoted form of healthURL & " >/dev/null"
		return true
	on error
		return false
	end try
end endpointReady

on stopOwnedTunnel(pidFile, portSignature)
	set commandText to "if [ -s " & quoted form of pidFile & " ]; then " & ¬
		"pid=$(/bin/cat " & quoted form of pidFile & " 2>/dev/null || true); " & ¬
		"case \"$pid\" in ''|*[!0-9]*) ;; *) " & ¬
		"command=$(/bin/ps -p \"$pid\" -o command= 2>/dev/null || true); " & ¬
		"case \"$command\" in *" & quoted form of portSignature & "*) /bin/kill \"$pid\" 2>/dev/null || true ;; esac ;; esac; fi; " & ¬
		"/bin/rm -f " & quoted form of pidFile
	do shell script commandText
end stopOwnedTunnel

on run
	set localPort to "16087"
	set remotePort to "6087"
	set sshHosts to {"lachlan@lachlanserver.local", "lachlan@lachlanserver", "lachlan@192.168.1.99", "lachlan@192.168.24.108"}
	set healthURL to "http://127.0.0.1:" & localPort & "/vnc.html"
	set controllerURL to healthURL & "?host=127.0.0.1&port=" & localPort & "&autoconnect=1&resize=scale"
	set cacheDirectory to POSIX path of (path to library folder from user domain) & "Caches/AstrillLazyRouter"
	set pidFile to cacheDirectory & "/tunnel.pid"
	set logFile to cacheDirectory & "/tunnel.log"
	set portSignature to "127.0.0.1:" & localPort & ":127.0.0.1:" & remotePort

	if endpointReady(healthURL) then
		open location controllerURL
		return
	end if

	do shell script "/bin/mkdir -p " & quoted form of cacheDirectory & " && /bin/chmod 700 " & quoted form of cacheDirectory
	stopOwnedTunnel(pidFile, portSignature)

	repeat with sshHost in sshHosts
		set tunnelCommand to "/usr/bin/nohup /usr/bin/ssh -N " & ¬
			"-o BatchMode=yes -o StrictHostKeyChecking=accept-new " & ¬
			"-o ConnectTimeout=4 -o ConnectionAttempts=1 " & ¬
			"-o ExitOnForwardFailure=yes -o ServerAliveInterval=30 " & ¬
			"-o ServerAliveCountMax=3 -L " & portSignature & " " & ¬
			quoted form of (contents of sshHost) & " </dev/null >" & ¬
			quoted form of logFile & " 2>&1 & echo $! >" & quoted form of pidFile
		do shell script tunnelCommand

		repeat 16 times
			if endpointReady(healthURL) then
				open location controllerURL
				return
			end if
			delay 0.25
		end repeat
		stopOwnedTunnel(pidFile, portSignature)
	end repeat

	set diagnostic to ""
	try
		set diagnostic to do shell script "/usr/bin/tail -n 4 " & quoted form of logFile
	end try
	display alert "Astrill Lazy Router" message "Could not reach the secure controller." & return & return & diagnostic as critical
end run
