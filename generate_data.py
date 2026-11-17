"""Generate the SYNTHETIC IT ticket dataset and SOP knowledge base.

Everything written by this script is generated, not real: ticket text, labels,
timestamps, handling times, resolutions and the restricted flag. Every row carries
label_source = "synthetic-generated" so this is visible in the data itself.

Run:  python generate_data.py
Out:  data/tickets.csv, data/sops.csv
"""
import csv
import os
import random
from datetime import datetime, timedelta

SEED = 42
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

LOCATIONS = ["Hyderabad", "Chennai", "Noida", "Bengaluru", "Pune", "Remote"]
DEPARTMENTS = ["Finance", "Sales", "Engineering", "HR", "Operations", "Marketing"]
BUSINESS_APPS = ["SAP ERP", "Salesforce CRM", "Jira", "Workday HR", "Payroll Portal", "Tableau", "Git Enterprise"]
SERVER_APPS = BUSINESS_APPS + ["Intranet Portal", "File Server"]
SENSITIVE_APPS = {"Workday HR", "Payroll Portal"}
FIRST = ["Aarav", "Diya", "Rohan", "Sneha", "Vikram", "Priya", "Karthik", "Meera", "Arjun", "Nisha", "Rahul", "Ananya",
         "Suresh", "Kavya", "Imran", "Lakshmi", "Deepak", "Pooja", "Manoj", "Divya", "Sanjay", "Ritu", "Naveen", "Farah"]
LAST = ["Sharma", "Reddy", "Iyer", "Khan", "Patel", "Nair", "Gupta", "Rao", "Menon", "Singh", "Das", "Verma"]

# sop_id, title, category, risk, requires_approval, fix-time range (minutes), steps
SOPS = [
    ("SOP-001", "Renew VPN client certificate", "VPN", "Low", 0, (10, 30), [
        "Confirm the error text mentions certificate validation or an expired certificate.",
        "Check the certificate expiry date in the user certificate store.",
        "Trigger certificate re-enrolment from the device management console.",
        "Ask the user to restart the VPN client and reconnect.",
        "Verify the tunnel is up and an internal site opens."]),
    ("SOP-002", "Re-register MFA device for VPN sign-in", "VPN", "Low", 0, (10, 30), [
        "Verify the user's identity using the service desk verification questions.",
        "Check the MFA service status page for a wider outage.",
        "Remove the stale authenticator registration for the user.",
        "Guide the user through registering the authenticator app again.",
        "Have the user connect to VPN and approve the push notification."]),
    ("SOP-003", "Reset VPN profile and update client", "VPN", "Low", 0, (15, 40), [
        "Collect the VPN client log from the user's device.",
        "Check the client version against the supported version.",
        "Delete the saved VPN profile and upgrade the client if outdated.",
        "Lower the tunnel MTU to 1300 if the user is on a home broadband router.",
        "Monitor the session for 20 minutes to confirm it stays connected."]),
    ("SOP-004", "Move user to nearest VPN gateway and enable split tunnelling", "VPN", "Low", 0, (15, 40), [
        "Run a speed test with VPN off and on to confirm the slowdown is VPN only.",
        "Check current gateway load on the VPN dashboard.",
        "Assign the user to the nearest regional gateway.",
        "Enable the approved split-tunnel policy for collaboration traffic.",
        "Re-test file share and application response times."]),
    ("SOP-005", "Reinstall Wi-Fi profile and machine certificate", "Wi-Fi", "Low", 0, (10, 30), [
        "Confirm the device is failing 802.1X authentication on the corporate SSID.",
        "Check that the device account is enabled in the directory.",
        "Remove the saved corporate Wi-Fi profile.",
        "Push the Wi-Fi profile and machine certificate again from device management.",
        "Reconnect and confirm an IP address is assigned."]),
    ("SOP-006", "Reinstall wireless adapter driver", "Wi-Fi", "Low", 0, (15, 45), [
        "Check Device Manager for a disabled or missing wireless adapter.",
        "Roll back the most recent driver update if one was installed.",
        "Install the approved wireless driver package for the laptop model.",
        "Restart the device.",
        "Confirm wireless networks are listed and the corporate SSID connects."]),
    ("SOP-007", "Access point health check and channel rebalance", "Wi-Fi", "Medium", 0, (30, 90), [
        "Identify the access points serving the reported floor or room.",
        "Check client count, signal strength and error rate on the wireless controller.",
        "Rebalance channels and transmit power for overloaded access points.",
        "Restart a faulty access point outside meeting hours if needed.",
        "Walk-test the area or ask reporters to confirm stable connectivity."]),
    ("SOP-008", "Rebuild Outlook profile", "Email", "Low", 0, (15, 40), [
        "Confirm webmail works, to rule out a mailbox or server issue.",
        "Close Outlook and back up any local PST files.",
        "Create a new Outlook profile and set it as default.",
        "Let the mailbox resynchronise.",
        "Send and receive a test email."]),
    ("SOP-009", "Release sender from outbound spam block", "Email", "Medium", 0, (20, 60), [
        "Read the bounce message and confirm the 550 5.7.1 outbound block.",
        "Check the restricted senders list in the mail security console.",
        "Scan the user's device and review recent sent items for compromise.",
        "Remove the sender from the restricted list.",
        "Send a test email to an external address and confirm delivery."]),
    ("SOP-010", "Enable online archive and raise mailbox quota", "Email", "Low", 0, (10, 30), [
        "Check current mailbox size against its quota.",
        "Enable the online archive for the mailbox.",
        "Apply the standard archive policy for items older than one year.",
        "Raise the quota temporarily if the user cannot send.",
        "Confirm the user can send and that archiving has started."]),
    ("SOP-011", "Phishing response: quarantine and credential reset", "Email", "Medium", 0, (20, 60), [
        "Ask the user to forward the message as an attachment and not click anything further.",
        "Search for and quarantine the same message in all mailboxes.",
        "Block the sender domain and URL in the mail security console.",
        "If a link was clicked or a password entered, reset the password and revoke sessions.",
        "Record the incident for the security team."]),
    ("SOP-012", "Verified password reset", "Access", "Low", 0, (5, 15), [
        "Verify the user's identity using the service desk verification questions.",
        "Reset the password in the directory and force a change at next sign-in.",
        "Share the temporary password through the approved channel.",
        "Confirm the user can sign in.",
        "Remind the user to update saved passwords on phone and other devices."]),
    ("SOP-013", "Unlock account and clear stale credentials", "Access", "Low", 0, (10, 30), [
        "Verify the user's identity.",
        "Find the lockout source device in the directory lockout log.",
        "Clear saved credentials on that device (mapped drives, phone mail, credential manager).",
        "Unlock the account.",
        "Watch for 30 minutes to confirm the account does not lock again."]),
    ("SOP-014", "Grant shared folder access with owner approval", "Access", "Medium", 1, (20, 90), [
        "Identify the folder and its data owner.",
        "Request written approval from the data owner.",
        "Add the user to the folder's security group with the approved permission level.",
        "Ask the user to sign out and back in.",
        "Confirm the folder opens and attach the approval to the ticket."]),
    ("SOP-015", "Application role assignment with manager approval", "Access", "Medium", 1, (20, 90), [
        "Confirm which role or module the user needs.",
        "Request approval from the user's manager and the application owner.",
        "Assign the role in the application's user administration screen.",
        "Ask the user to sign in again.",
        "Confirm the module is visible and attach the approval to the ticket."]),
    ("SOP-016", "Power drain and hardware diagnostics", "Hardware", "Low", 0, (20, 60), [
        "Disconnect the charger and all peripherals.",
        "Hold the power button for 30 seconds to drain residual power.",
        "Reconnect the charger only and power on.",
        "Run the built-in hardware diagnostics if the device starts.",
        "Raise a hardware replacement if diagnostics fail or the device stays dead."]),
    ("SOP-017", "BitLocker recovery key retrieval", "Hardware", "Medium", 0, (10, 30), [
        "Verify the user's identity.",
        "Note the recovery key ID shown on the BitLocker screen.",
        "Look up the matching recovery key in the directory.",
        "Read the key to the user and confirm the device unlocks.",
        "Check why recovery was triggered (BIOS or TPM change) and re-seal the key."]),
    ("SOP-018", "Dock firmware and display driver refresh", "Hardware", "Low", 0, (15, 45), [
        "Reseat the dock cable and test the monitor with a direct cable.",
        "Update the docking station firmware.",
        "Update the graphics driver to the approved version.",
        "Power-cycle the dock.",
        "Confirm displays and USB peripherals are detected."]),
    ("SOP-019", "Thermal check and battery replacement", "Hardware", "Low", 0, (30, 120), [
        "Check for processes using high CPU that explain the heat.",
        "Run the battery health report.",
        "Clean vents and apply the latest BIOS and thermal firmware.",
        "Replace the battery if health is below 60 percent.",
        "Confirm temperature and battery runtime are back to normal."]),
    ("SOP-020", "Clear application cache and repair installation", "Application", "Low", 0, (15, 45), [
        "Capture the crash message or event log entry.",
        "Close the application and clear its local cache folder.",
        "Run a repair of the installation from the software centre.",
        "Reinstall the latest approved version if repair fails.",
        "Launch the application and confirm it stays open."]),
    ("SOP-021", "Clear SSO session and resynchronise identity", "Application", "Low", 0, (10, 30), [
        "Reproduce the sign-in loop in a private browser window.",
        "Clear browser cookies and cached SSO tokens.",
        "Check the user's application account is active and matches the directory email.",
        "Trigger an identity resynchronisation for the user.",
        "Confirm the user lands on the application home page."]),
    ("SOP-022", "Assign application licence", "Application", "Low", 0, (10, 40), [
        "Confirm the error states that no licence is assigned.",
        "Check available licences in the licence pool.",
        "Add the user to the application's licence group.",
        "Wait for the licence to synchronise.",
        "Ask the user to sign out and in, then confirm access."]),
    ("SOP-023", "Application performance triage", "Application", "Medium", 0, (30, 120), [
        "Confirm whether slowness affects one user or many.",
        "Check application server and database response times on the monitoring dashboard.",
        "Review the database connection pool and long-running queries.",
        "Recycle the application pool in a quiet period if connections are exhausted.",
        "Confirm page and report load times are back within the normal range."]),
    ("SOP-024", "Controlled application server restart", "Server", "High", 1, (45, 150), [
        "Confirm the service is down from monitoring and capture the error (for example HTTP 503).",
        "Check active user sessions and running batch jobs on the server.",
        "Notify affected stakeholders and post an outage notice.",
        "Obtain approval from the change manager or on-call lead.",
        "Take a configuration backup or VM snapshot.",
        "Restart the application service first; restart the server only if the service restart fails.",
        "Verify health checks, sign-in and one key transaction.",
        "Rollback: if the service does not recover in 15 minutes, restore the snapshot and escalate to L3."]),
    ("SOP-025", "Roll back failed deployment", "Server", "High", 1, (45, 180), [
        "Confirm the failure started right after the latest release.",
        "Collect error logs and the release identifier.",
        "Notify the release owner and affected stakeholders.",
        "Obtain approval from the change manager.",
        "Redeploy the previous stable build using the deployment pipeline.",
        "Verify health checks and one key transaction.",
        "Rollback of the rollback: if the previous build also fails, restore the last database backup with L3."]),
    ("SOP-026", "Identify runaway process and recycle it", "Server", "Medium", 0, (30, 120), [
        "Confirm the CPU or memory alert on the monitoring dashboard.",
        "Identify the process consuming resources.",
        "Capture a process dump for the application team.",
        "Recycle that process or application pool; do not restart the whole server.",
        "Watch CPU and memory for 30 minutes."]),
    ("SOP-027", "Log cleanup and disk extension", "Server", "Medium", 0, (30, 120), [
        "Confirm which volume is full and what is consuming the space.",
        "Compress and archive application logs older than 14 days.",
        "Clear temporary files using the approved cleanup script.",
        "Extend the volume if free space is still under 15 percent.",
        "Confirm the alert has cleared and set up log rotation."]),
    ("SOP-028", "Clear print spooler and re-map printer", "Printer", "Low", 0, (10, 30), [
        "Check the printer is powered on and online on the print server.",
        "Clear stuck jobs and restart the print spooler service.",
        "Remove and re-add the printer on the user's device.",
        "Reinstall the printer driver if jobs still stall.",
        "Print a test page."]),
    ("SOP-029", "Printer maintenance and toner replacement", "Printer", "Low", 0, (20, 60), [
        "Ask the user to open the trays and remove jammed paper following the panel instructions.",
        "Run the printer's cleaning cycle.",
        "Replace the toner or drum if prints are faded or streaked.",
        "Raise a vendor visit if the jam repeats.",
        "Print a test page."]),
    ("SOP-030", "Escalate to vendor or L3 with diagnostics", "General", "Medium", 0, (120, 480), [
        "Record what was tried and the result of each step.",
        "Collect logs, screenshots and exact error text.",
        "Raise the case with the vendor or L3 team and link it to the ticket.",
        "Update the user with the reference and expected timeline.",
        "Follow up until the vendor or L3 confirms the fix."]),
]

GROUPS = {"VPN": "Network Support", "Wi-Fi": "Network Support", "Email": "Messaging Team",
          "Access": "Identity and Access", "Hardware": "Desktop Support", "Application": "Application Support",
          "Server": "Server Operations", "Printer": "Print Services"}

# Each variant = one root cause, with its own wording and its own SOP.
V = []
def variant(cat, sub, sop, weight, apps, titles, descs, notes, alt=None, multi=True):
    V.append(dict(cat=cat, sub=cat + " - " + sub, sop=sop, weight=weight, apps=apps, titles=titles,
                  descs=descs, notes=notes, alt=alt, multi=multi))

variant("VPN", "Cannot connect", "SOP-001", 1.2, ["GlobalProtect VPN"],
    ["VPN not connecting - certificate error", "Cannot connect to VPN", "VPN certificate validation failure"],
    ["When I try to connect to the VPN the client shows certificate validation failure and stops.",
     "VPN client gives an error that the certificate on the secure gateway is invalid or expired and will not connect.",
     "GlobalProtect says required client certificate not found. I cannot connect to VPN from {loc}.",
     "VPN connection fails at the certificate step every time, error says certificate has expired."],
    ["Client certificate had expired. Re-enrolled the certificate and user connected.",
     "Renewed the VPN client certificate from device management, tunnel is up."], alt="SOP-002")
variant("VPN", "Cannot connect", "SOP-002", 1.0, ["GlobalProtect VPN"],
    ["VPN login - no MFA prompt", "Cannot connect to VPN, authenticator not prompting", "VPN sign-in times out"],
    ["I am not getting the MFA push notification on my phone when I connect to the VPN, so the login times out.",
     "VPN asks for approval in the authenticator app but no prompt arrives. I changed my phone last week.",
     "VPN sign-in keeps waiting for multi-factor approval and then fails. The authenticator app shows nothing.",
     "Cannot connect to VPN because the MFA code is rejected and no push comes to my mobile."],
    ["Stale authenticator registration. Re-registered MFA and VPN sign-in worked.",
     "User had a new phone. Removed old MFA device, registered again, VPN connected."], alt="SOP-001")
variant("VPN", "Frequent disconnects", "SOP-003", 1.0, ["GlobalProtect VPN"],
    ["VPN keeps disconnecting", "VPN drops every few minutes", "VPN reconnecting loop"],
    ["The VPN drops every 10 to 15 minutes and I have to reconnect each time. It happens on my home broadband.",
     "VPN connects fine but disconnects again and again during calls. The client is stuck in a reconnecting loop.",
     "My VPN session keeps dropping while I work from home. It reconnects by itself after a minute and then drops again.",
     "VPN is unstable since the client update, it disconnects many times in an hour."],
    ["Deleted VPN profile, upgraded client and lowered MTU. Session stable for 30 minutes.",
     "Old client version. Updated client and reset profile, no more drops."])
variant("VPN", "Slow over VPN", "SOP-004", 0.9, ["GlobalProtect VPN"],
    ["Very slow when on VPN", "Slow file share over VPN", "VPN speed is poor"],
    ["Everything is very slow only when I am on VPN. File shares take minutes to open, without VPN my internet is fast.",
     "Network speed over VPN is poor, video calls freeze and shared drive copies crawl.",
     "Applications load very slowly over the VPN from {loc}. Speed test is fine when VPN is off.",
     "VPN is connected but the throughput is terrible, downloading a small file from the shared drive takes ages."],
    ["User was on a distant gateway. Moved to nearest gateway and enabled split tunnel, speed normal.",
     "Gateway overloaded. Reassigned gateway, response times back to normal."])
variant("Wi-Fi", "Cannot connect", "SOP-005", 1.1, ["Corporate Wi-Fi"],
    ["Cannot connect to office Wi-Fi", "Wi-Fi asking for credentials again and again", "Wi-Fi authentication failed"],
    ["My laptop cannot join the corporate Wi-Fi CORP-SECURE. It keeps asking for my username and password and then says cannot connect.",
     "Wi-Fi authentication failed on the office network at {loc}. Guest Wi-Fi works but the corporate SSID does not.",
     "Laptop will not connect to the office wireless, it says unable to connect to this network after entering credentials.",
     "Corporate Wi-Fi rejects my laptop since this morning, 802.1X authentication error in the log."],
    ["Machine certificate missing. Pushed Wi-Fi profile and certificate again, connected.",
     "Removed saved Wi-Fi profile and re-deployed it, device authenticated."], alt="SOP-006")
variant("Wi-Fi", "Cannot connect", "SOP-006", 0.8, ["Corporate Wi-Fi"],
    ["No Wi-Fi networks showing", "Wireless adapter missing after update", "Wi-Fi option disappeared"],
    ["No wireless networks are showing on my laptop at all. The Wi-Fi button has disappeared after last night's update.",
     "The wireless adapter seems to be missing, I see only the ethernet option and cannot connect to any Wi-Fi.",
     "After the Windows update the Wi-Fi is turned off and I cannot switch it on, the adapter shows a warning sign.",
     "Laptop does not detect any Wi-Fi network, at home or in the office. Adapter looks disabled."],
    ["Wireless driver corrupted by update. Reinstalled approved driver, networks visible.",
     "Rolled back the adapter driver and restarted, Wi-Fi working."], alt="SOP-005")
variant("Wi-Fi", "Intermittent drops", "SOP-007", 1.0, ["Corporate Wi-Fi"],
    ["Wi-Fi keeps dropping in meeting rooms", "Weak Wi-Fi signal on our floor", "Wireless unstable in office"],
    ["Wi-Fi keeps dropping in the meeting rooms on the {floor} floor at {loc}. The signal is weak and calls disconnect.",
     "The wireless connection in our bay is unstable, it drops for a few seconds every few minutes for everyone sitting here.",
     "Weak Wi-Fi signal near the {floor} floor conference area, laptops keep switching between access points.",
     "Office Wi-Fi at {loc} is intermittent since morning, many people on the {floor} floor are getting disconnected."],
    ["Access point overloaded. Rebalanced channels and power, signal stable.",
     "Faulty access point restarted and channel changed, drops stopped."])
variant("Email", "Cannot send or receive", "SOP-008", 1.1, ["Outlook"],
    ["Outlook disconnected", "Not receiving emails in Outlook", "Outlook stuck on trying to connect"],
    ["Outlook shows disconnected at the bottom and I have not received any new mail since morning. Webmail works.",
     "My Outlook is stuck on trying to connect and emails stay in the outbox. I can see the mails on my phone.",
     "Outlook is not updating the inbox, the folder says last updated yesterday. Restarting Outlook does not help.",
     "Emails are not syncing in Outlook desktop. It asks for the password repeatedly and stays disconnected."],
    ["Corrupt Outlook profile. Created new profile, mailbox synced.",
     "Rebuilt the Outlook profile, send and receive working."], alt="SOP-009")
variant("Email", "Cannot send or receive", "SOP-009", 0.8, ["Outlook"],
    ["Emails to external addresses bouncing", "NDR 550 5.7.1 on outgoing mail", "Cannot send email outside company"],
    ["Every email I send to external recipients bounces back with an undeliverable message 550 5.7.1. Internal mail is fine.",
     "My outgoing emails to customers are rejected, the bounce says the sender is blocked from sending outbound mail.",
     "I get a non-delivery report for all external emails saying access denied, bad outbound sender.",
     "Cannot send mail outside the company since yesterday, every message returns with NDR 550 5.7.1."],
    ["Sender was on the restricted list after a bulk send. Removed, external mail delivered.",
     "Outbound spam block released after device scan, test mail delivered."], alt="SOP-008")
variant("Email", "Mailbox full", "SOP-010", 0.9, ["Outlook"],
    ["Mailbox full", "Mailbox quota exceeded", "Cannot send - mailbox size limit"],
    ["I am getting a warning that my mailbox is full and I cannot send any emails. Please increase the size.",
     "Outlook says mailbox quota exceeded. I have deleted many mails but it still does not let me send.",
     "My mailbox has reached its size limit and new mails are being rejected.",
     "Mailbox is almost full message keeps appearing and now sending is blocked. I need the old mails for audit."],
    ["Enabled online archive and raised quota, user can send.",
     "Archive policy applied, mailbox size reduced below quota."])
variant("Email", "Phishing or spam report", "SOP-011", 0.9, ["Outlook"],
    ["Suspicious email received", "Possible phishing mail", "Clicked a link in a strange email"],
    ["I received a suspicious email asking me to verify my password through a link. It looks like it is from IT but the address is strange.",
     "Got a phishing mail with an invoice attachment from an unknown sender. Several colleagues got the same one.",
     "I clicked a link in an email that asked me to sign in and now I think it was fake. Please check.",
     "There is a spam email pretending to be from our CEO asking for gift cards. Reporting it."],
    ["Message quarantined across mailboxes, sender blocked, password reset as a precaution.",
     "Phishing confirmed. Purged the mail, blocked URL, user credentials reset."], multi=True)
variant("Access", "Password reset", "SOP-012", 1.3, ["Active Directory"],
    ["Password reset request", "Forgot my password", "Password expired"],
    ["I forgot my Windows password after my leave and cannot log in to my laptop. Please reset it.",
     "My password has expired and I am not able to change it from home. Need a reset.",
     "I do not remember my domain password, need it reset to sign in.",
     "Password is not accepted after the forced change yesterday, please reset my password."],
    ["Identity verified, password reset, user signed in.",
     "Reset password and forced change at next sign-in."], multi=False)
variant("Access", "Account locked", "SOP-013", 1.1, ["Active Directory"],
    ["Account locked out", "Account keeps getting locked", "Locked out of my account"],
    ["My account is locked out after too many attempts. It gets locked again a few minutes after it is unlocked.",
     "I keep getting account locked message on my laptop. I changed my password two days ago.",
     "The referenced account is currently locked out and may not be logged on to. This happens several times a day.",
     "My account locks repeatedly, I think my phone is still using the old password for mail."],
    ["Old password saved on phone mail app caused lockouts. Cleared it and unlocked.",
     "Found stale mapped drive credentials, cleared them, account stable."], multi=False)
variant("Access", "Permission request", "SOP-014", 0.9, ["File Server"],
    ["Need access to shared folder", "Access denied on shared drive", "Shared folder permission"],
    ["I need access to the {dept} shared folder on the file server. It says access denied when I open it.",
     "Please give me read and write permission to the project shared drive, my manager has asked me to work on those files.",
     "I get you do not have permission to access this folder for the {dept} team share.",
     "Requesting access to the shared folder for the new project, currently I cannot open it."],
    ["Owner approval received, user added to the folder security group.",
     "Approval attached, read-write access granted via group."], multi=False)
variant("Access", "Permission request", "SOP-015", 0.9, BUSINESS_APPS,
    ["Need role in {app}", "{app} - not authorised for module", "Access request for {app}"],
    ["I need the approver role in {app}. Right now it says you are not authorised to view this module.",
     "Please assign me access to the reporting module in {app}, I have moved to a new team.",
     "{app} shows insufficient privileges when I open the screen I need for my work. Requesting the role.",
     "New joiner in {dept}, requires a user role in {app} same as the rest of the team."],
    ["Manager and app owner approved, role assigned in {app}.",
     "Role added after approval, module visible to user."], multi=False)
variant("Hardware", "Laptop won't boot", "SOP-016", 0.9, ["Laptop"],
    ["Laptop not turning on", "Laptop black screen", "Laptop dead"],
    ["My laptop is not turning on at all. The power light blinks but the screen stays black.",
     "Laptop shows a black screen after I press the power button, the fan spins for a second and stops.",
     "The laptop will not start since morning, charger is connected and the charging light is on.",
     "My machine is dead, no display and no sound when I press power."],
    ["Power drain fixed it, diagnostics passed.",
     "Residual power drained, laptop booted. Hardware test clean."], alt="SOP-030", multi=False)
variant("Hardware", "Laptop won't boot", "SOP-017", 0.8, ["Laptop"],
    ["BitLocker recovery screen", "Laptop asking for BitLocker key", "Stuck at BitLocker recovery"],
    ["My laptop is stuck on the BitLocker recovery screen asking for a recovery key. I do not have any key.",
     "After a BIOS update the laptop asks for the BitLocker recovery key every time it starts.",
     "Laptop boots to a blue screen that says enter the recovery key for this drive.",
     "BitLocker is asking for a 48 digit key at startup and I cannot get into Windows."],
    ["Provided recovery key after verification, re-sealed TPM.",
     "Recovery key read to user, device unlocked, BIOS change was the trigger."], multi=False)
variant("Hardware", "Peripheral not working", "SOP-018", 1.0, ["Laptop"],
    ["External monitor not detected", "Docking station not working", "Keyboard and mouse not working via dock"],
    ["My external monitor is not detected when I connect the laptop to the docking station.",
     "The docking station is not working, second screen is blank and the USB keyboard does not respond.",
     "Monitors connected through the dock keep flickering and then show no signal.",
     "Keyboard and mouse plugged into the dock stopped working after the laptop woke from sleep."],
    ["Updated dock firmware and graphics driver, displays detected.",
     "Dock power-cycled and firmware updated, peripherals working."], multi=False)
variant("Hardware", "Battery or overheating", "SOP-019", 0.8, ["Laptop"],
    ["Laptop overheating", "Battery draining very fast", "Laptop fan very loud"],
    ["My laptop gets very hot and the fan is loud all the time. The battery drains in about an hour.",
     "Battery does not last, it goes from full to 20 percent in less than an hour and the bottom is hot.",
     "Laptop is overheating and shuts down by itself during video calls.",
     "The battery shows plugged in, not charging and the laptop feels very warm."],
    ["Battery health 41 percent, battery replaced.",
     "Cleaned vents, updated BIOS, replaced battery. Temperature normal."], multi=False)
variant("Application", "Crash on launch", "SOP-020", 1.1, BUSINESS_APPS,
    ["{app} crashes on opening", "{app} closes immediately", "{app} not launching after update"],
    ["{app} closes immediately after I open it. This started after the latest update.",
     "{app} crashes on launch with an unexpected error message and I cannot do my work in it.",
     "The {app} desktop client shows the splash screen and then quits by itself.",
     "{app} stopped launching today, it worked yesterday. I get a has stopped working popup."],
    ["Cleared the application cache and repaired the install, launches fine.",
     "Corrupt cache. Repair install fixed the crash."], alt="SOP-030")
variant("Application", "Login error", "SOP-021", 1.1, BUSINESS_APPS,
    ["{app} login loop", "Cannot sign in to {app}", "{app} SSO error"],
    ["{app} single sign-on keeps looping back to the sign-in page. I enter my credentials and it returns to the same page.",
     "I get a SAML error when I sign in to {app} through the company portal.",
     "Cannot log in to {app}, after SSO it shows we could not sign you in and sends me back.",
     "{app} login is failing for me since morning, the page refreshes endlessly after authentication."],
    ["Cleared SSO tokens and resynced identity, user signed in.",
     "Account email mismatch fixed with identity resync, login works."], alt="SOP-022")
variant("Application", "Login error", "SOP-022", 0.8, BUSINESS_APPS,
    ["{app} says no licence", "{app} licence not assigned", "No valid licence for {app}"],
    ["{app} says no valid licence is assigned to my account when I sign in.",
     "I can sign in but {app} shows your account does not have a licence for this product.",
     "After moving teams {app} tells me licence not found and I cannot open anything.",
     "{app} sign in works but then it shows a licence required message."],
    ["Added user to licence group, access restored after sync.",
     "Licence assigned from pool, user confirmed access."], alt="SOP-021")
variant("Application", "Slow performance", "SOP-023", 1.0, BUSINESS_APPS,
    ["{app} very slow", "{app} reports timing out", "{app} pages take minutes to load"],
    ["{app} is very slow today, every page takes minutes to load and reports time out.",
     "Reports in {app} are timing out and screens hang while saving. Others in my team see the same.",
     "{app} performance is terrible since morning, searches spin for a long time before showing results.",
     "Saving a record in {app} takes more than a minute and sometimes fails with a timeout."],
    ["Connection pool exhausted. Recycled app pool in quiet period, response normal.",
     "Long-running query killed and pool recycled, load times normal."], alt="SOP-026")
variant("Server", "Service down", "SOP-024", 1.2, SERVER_APPS,
    ["{app} is down", "{app} service unavailable 503", "{app} server not responding"],
    ["{app} is down, the page shows 503 service unavailable. The server is not responding to health checks.",
     "Nobody can open {app}. Monitoring shows the application service has stopped responding on the production server.",
     "{app} server is not reachable since a few minutes, users get service unavailable error.",
     "{app} is completely unavailable, the application service looks hung and health check is failing."],
    ["Followed controlled restart with approval. Service restart restored {app}, health checks green.",
     "Approval taken, snapshot done, application service restarted. Verified sign-in and a transaction."], alt="SOP-025")
variant("Server", "Service down", "SOP-025", 0.9, SERVER_APPS,
    ["{app} failing after release", "{app} errors after last night's deployment", "{app} down post deployment"],
    ["{app} started throwing errors right after last night's deployment. The service stops a few seconds after starting.",
     "After the new release {app} is failing with 500 errors on every request.",
     "{app} has been down since the deployment in the morning, the new build does not start.",
     "Post release, {app} service crashes on startup with a configuration error."],
    ["Release was faulty. Rolled back to previous stable build with approval, service healthy.",
     "Rollback done through pipeline after change manager approval, errors stopped."], alt="SOP-024")
variant("Server", "High CPU or memory", "SOP-026", 1.0, SERVER_APPS,
    ["High CPU on {app} server", "Memory alert on {app} server", "{app} server CPU at 100 percent"],
    ["Monitoring alert: CPU is at 100 percent on the {app} production server for the last 20 minutes.",
     "Memory usage on the {app} server is above 95 percent and one process keeps growing.",
     "The {app} server is sluggish, a single worker process is consuming almost all CPU.",
     "High memory alert on the {app} application server, looks like a memory leak in one process."],
    ["Runaway worker process recycled after capturing dump, CPU normal.",
     "Identified leaking process, recycled it, memory back to 60 percent."], alt="SOP-024")
variant("Server", "Disk space full", "SOP-027", 1.0, SERVER_APPS,
    ["Disk space alert on {app} server", "{app} server disk full", "Low disk space on {app} server"],
    ["Low disk space alert: the data volume on the {app} server is at 98 percent. Logs are filling it.",
     "The {app} server disk is full and the application cannot write files.",
     "Disk usage on the {app} server crossed 95 percent, log folder has grown very large.",
     "No space left on device errors on the {app} server, the var partition is full."],
    ["Archived old logs and ran cleanup script, 35 percent free.",
     "Cleared logs and extended the volume, alert cleared, rotation configured."])
variant("Printer", "Cannot print", "SOP-028", 1.0, ["Print Service"],
    ["Printer offline", "Print jobs stuck in queue", "Cannot print to floor printer"],
    ["The printer on the {floor} floor shows offline and my print jobs are stuck in the queue.",
     "I cannot print, documents stay in the queue with status error.",
     "Print jobs are not coming out of the {loc} office printer, the queue keeps growing.",
     "Printer is not responding from my laptop, it says printer offline though it is switched on."],
    ["Cleared spooler and re-mapped printer, test page printed.",
     "Stuck job removed, spooler restarted, printing works."])
variant("Printer", "Paper jam or quality", "SOP-029", 0.8, ["Print Service"],
    ["Printer paper jam", "Faded prints", "Streaks on printed pages"],
    ["The printer on the {floor} floor has a paper jam and shows an error on the panel.",
     "Prints are coming out faded and with black streaks from the {loc} office printer.",
     "Paper gets jammed every few pages in the printer near the pantry.",
     "Print quality is very poor, half the page is blank and toner low is blinking."],
    ["Removed jam, ran cleaning cycle, replaced toner.",
     "Toner and drum replaced, print quality normal."])

OPENERS = ["Hi team,", "Hello,", "", "", "Good morning,", "Hi,", "Urgent -", "Dear support,"]
CONTEXT = ["It was working fine yesterday.", "I have already restarted my laptop twice.",
           "This started after the weekend patching.", "I am working from home today.",
           "I have a client call in an hour.", "I tried from another network as well.", "",
           "Please treat this on priority.", "I have attached a screenshot.", "", "Not sure what changed.",
           "My colleague suggested raising a ticket."]
IMPACT_PHRASES = {
    1: ["Only I seem to be affected.", "My colleagues are working fine.", "It is just my machine.", ""],
    2: ["Three of us in my team have the same problem.", "My whole team is facing this.", "A few people sitting near me have it too."],
    3: ["Everyone in the {dept} department is affected.", "The entire {loc} office is reporting this.", "Around 60 users on our floor are impacted."],
    4: ["All users across the company are impacted.", "This is affecting every office.", "The whole organisation is unable to use it."],
}
URGENCY_PHRASES = {
    "Blocked": ["I cannot do any work until this is fixed.", "This is completely blocking us.", "Work has stopped because of this."],
    "Degraded": ["I can still work but it is very slow.", "We are managing with a workaround for now.", "It is slowing us down a lot."],
    "Minor": ["Not urgent, please look when possible.", "Low priority from my side.", "It is a small annoyance."],
}
VAGUE = {"VPN": ["vpn not working pls help", "VPN issue. Please check asap."],
         "Wi-Fi": ["wifi problem, please help", "Wi-Fi not working properly."],
         "Email": ["email issue please check", "Problem with my mails."],
         "Access": ["cannot login, help needed", "Access issue."],
         "Hardware": ["laptop problem", "My laptop is not working properly, please help."],
         "Application": ["{app} not working", "Issue in {app}, please look into it."],
         "Server": ["{app} issue, please check urgently", "{app} having problems."],
         "Printer": ["printer problem", "Printing not working."]}
# Tickets whose wording fits two categories. Real users write like this, and it is what makes classification hard.
AMBIG = {
    ("VPN", "Wi-Fi"): (["Connection problem", "Network issue", "Cannot connect"],
                       ["I cannot connect to the network from my laptop since morning, it just keeps trying.",
                        "My internet connection keeps dropping and I get disconnected from everything.",
                        "The connection is very slow today, pages and shared drives take very long to open."]),
    ("Application", "Server"): (["{app} issue", "{app} not working", "Problem with {app}"],
                                ["{app} is not working, I get an error page when I open it.",
                                 "{app} is throwing errors and nobody in my team can use it properly.",
                                 "{app} is extremely slow and sometimes does not load at all."]),
    ("Application", "Access"): (["Cannot log in to {app}", "{app} login problem"],
                                ["I cannot log in to {app}, it does not accept my credentials.",
                                 "{app} says access denied when I try to sign in."]),
    ("Email", "Access"): (["Mail login problem", "Cannot sign in to email"],
                          ["I cannot sign in to my mail, it keeps asking for the password and rejects it."]),
    ("Hardware", "Wi-Fi"): (["Laptop not connecting", "Laptop issue after sleep"],
                            ["My laptop does not connect to anything after it wakes up from sleep."]),
}
# Problems that keep coming back on the same service and site: (subcategory, SOP, application, location, every N days, occurrences)
SERIES = [
    ("Server - Disk space full", "SOP-027", "Tableau", "Pune", 9, 11),
    ("Wi-Fi - Intermittent drops", "SOP-007", "Corporate Wi-Fi", "Noida", 7, 14),
    ("Server - High CPU or memory", "SOP-026", "SAP ERP", "Hyderabad", 12, 8),
    ("Printer - Cannot print", "SOP-028", "Print Service", "Bengaluru", 6, 15),
    ("Application - Slow performance", "SOP-023", "Salesforce CRM", "Chennai", 10, 10),
]
EVENTS = ["firewall change", "network maintenance window", "weekend patching", "datacentre power test",
          "ISP outage", "certificate rotation", "storage migration", "switch replacement"]
PRIORITY_MATRIX = {("Blocked", 4): "P1", ("Blocked", 3): "P1", ("Blocked", 2): "P2", ("Blocked", 1): "P3",
                   ("Degraded", 4): "P1", ("Degraded", 3): "P2", ("Degraded", 2): "P3", ("Degraded", 1): "P3",
                   ("Minor", 4): "P2", ("Minor", 3): "P3", ("Minor", 2): "P4", ("Minor", 1): "P4"}
WAIT = {"P1": (5, 20), "P2": (20, 90), "P3": (60, 360), "P4": (180, 1200)}
IMPACT_WEIGHTS = {"Server": [1, 3, 4, 3], "Application": [4, 3, 2, 1], "Wi-Fi": [4, 3, 2, 0.5], "VPN": [5, 2, 1, 0.5],
                  "Email": [5, 2, 1, 0.5], "Printer": [3, 4, 1, 0], "Access": [9, 1, 0, 0], "Hardware": [10, 0.3, 0, 0]}
URGENCY_WEIGHTS = {"Server": [6, 3, 1], "default": [4, 4, 2]}
FIELDS = ["ticket_id", "title", "description", "requester", "application", "environment", "location",
          "user_category", "category", "subcategory", "impact", "urgency", "priority", "status",
          "assignment_group", "created_at", "resolved_at", "resolution_sop", "resolution_notes", "restricted",
          "dup_group", "wait_minutes", "triage_minutes", "lookup_minutes", "fix_minutes", "first_attempt_sop",
          "reopen_count", "label_source"]
FMT = "%Y-%m-%d %H:%M:%S"


def typo(rng, text):
    words = text.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 5 and w.isalpha()]
    if not idx:
        return text
    i = rng.choice(idx)
    w = words[i]
    j = rng.randrange(1, len(w) - 2)
    words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


def main():
    rng = random.Random(SEED)
    os.makedirs(DATA, exist_ok=True)
    now = datetime.now().replace(microsecond=0)
    sop_fix = {s[0]: s[5] for s in SOPS}
    cats = sorted(GROUPS)
    rows = []

    def make(v, created, status, loc=None, app=None, impact=None, urgency=None, extra=None, dup=None, resolved_at=None):
        cat = v["cat"]
        loc = loc or rng.choice(LOCATIONS)
        app = app or rng.choice(v["apps"])
        slots = dict(app=app, loc=loc, dept=rng.choice(DEPARTMENTS), floor=rng.choice(["2nd", "3rd", "4th", "5th", "7th"]))
        if impact is None:
            impact = rng.choices([1, 2, 3, 4], weights=IMPACT_WEIGHTS[cat])[0]
        if urgency is None:
            urgency = rng.choices(["Blocked", "Degraded", "Minor"], weights=URGENCY_WEIGHTS.get(cat, URGENCY_WEIGHTS["default"]))[0]
        vague = extra is None and rng.random() < 0.05
        ambig = None
        if not vague and extra is None and rng.random() < 0.08:
            pairs = [p for p in AMBIG if cat in p]
            if pairs:
                ambig = rng.choice(pairs)
                if slots["app"] not in SERVER_APPS:
                    slots["app"] = rng.choice(BUSINESS_APPS)
        if vague:
            title = rng.choice(VAGUE[cat]).format(**slots).split(",")[0].split(".")[0]
            desc = rng.choice(VAGUE[cat]).format(**slots)
        else:
            if ambig:
                title = rng.choice(AMBIG[ambig][0]).format(**slots)
                core = rng.choice(AMBIG[ambig][1]).format(**slots)
            else:
                title = rng.choice(v["titles"]).format(**slots)
                core = rng.choice(v["descs"]).format(**slots)
            parts = [rng.choice(OPENERS), core]
            if extra:
                parts.append(extra)
            tail = [rng.choice(CONTEXT)]
            if rng.random() < 0.75:
                tail.append(rng.choice(IMPACT_PHRASES[impact]).format(**slots))
            if rng.random() < 0.75:
                tail.append(rng.choice(URGENCY_PHRASES[urgency]))
            rng.shuffle(tail)
            desc = " ".join(p for p in parts + tail if p)
            if rng.random() < 0.10:
                desc = typo(rng, desc)
        restricted = 0
        if app in SENSITIVE_APPS and rng.random() < 0.6:
            restricted = 1
            desc += " The attached screenshot contains employee salary details."
        elif v["sop"] == "SOP-014" and rng.random() < 0.15:
            restricted = 1
            desc += " The folder holds confidential board papers."
        r = rng.random()
        user_cat = cat if r < 0.86 else ("Not sure" if r < 0.94 else rng.choice([c for c in cats if c != cat]))
        env = "Production"
        if cat in ("Server", "Application") and rng.random() < 0.2:
            env = rng.choice(["UAT", "Development"])
        priority = PRIORITY_MATRIX[(urgency, impact)]
        row = dict(title=title, description=desc, requester=rng.choice(FIRST) + " " + rng.choice(LAST), application=app,
                   environment=env, location=loc, user_category=user_cat, category=cat, subcategory=v["sub"],
                   impact=impact, urgency=urgency, priority=priority, status=status, assignment_group=GROUPS[cat],
                   created_at=created, resolved_at="", resolution_sop="", resolution_notes="", restricted=restricted,
                   dup_group=dup or "", wait_minutes="", triage_minutes="", lookup_minutes="", fix_minutes="",
                   first_attempt_sop="", reopen_count=0,
                   label_source="synthetic-generated")
        if status in ("Resolved", "Closed"):
            r = rng.random()
            sop = v["sop"]
            if dup is None and not vague:
                if r > 0.95:
                    sop = "SOP-030"
                elif r > 0.85 and v["alt"]:
                    sop = v["alt"]
            wait = rng.randint(*WAIT[priority])
            triage = rng.randint(5, 25)
            lookup = rng.randint(10, 60)
            fix = rng.randint(*sop_fix[sop])
            if sop != v["sop"]:
                # the usual procedure was tried first and failed, so the ticket was reopened
                row.update(first_attempt_sop=v["sop"], reopen_count=1)
                fix += rng.randint(*sop_fix[v["sop"]])
            note = rng.choice(v["notes"]).format(**slots) if sop == v["sop"] else (
                "First tried %s; it did not fix the problem. Reopened, escalated with logs, vendor resolved it." % v["sop"]
                if sop == "SOP-030" else
                "First tried %s; it did not fix the problem. Reopened and resolved with the alternative procedure." % v["sop"])
            end = resolved_at or created + timedelta(minutes=wait + triage + lookup + fix)
            row.update(resolved_at=end, resolution_sop=sop, resolution_notes=note, wait_minutes=wait,
                       triage_minutes=triage, lookup_minutes=lookup, fix_minutes=fix)
        rows.append(row)
        return row

    def business_time(days_lo, days_hi):
        # Mondays and mid-mornings are busiest; weekends are quiet
        while True:
            d = now - timedelta(days=rng.uniform(days_lo, days_hi))
            if rng.random() < [1.0, 0.7, 0.65, 0.65, 0.55, 0.08, 0.05][d.weekday()]:
                break
        hour = rng.choices(list(range(7, 20)), weights=[1, 3, 6, 7, 6, 4, 3, 4, 4, 3, 2, 1, 1])[0]
        return d.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59))

    # 1. Individual resolved tickets
    for v in V:
        for _ in range(int(round(32 * v["weight"]))):
            make(v, business_time(2, 120), rng.choice(["Resolved", "Resolved", "Closed"]))

    # 2. Resolved duplicate clusters: several users reporting one incident
    multi = [v for v in V if v["multi"]]
    for g in range(36):
        v = rng.choice(multi)
        loc = rng.choice(LOCATIONS[:-1])
        app = rng.choice(v["apps"])
        t0 = business_time(3, 110)
        event = rng.choice(EVENTS)
        size = rng.randint(4, 9)
        impact = rng.choice([2, 3, 3, 4])
        fixed_at = t0 + timedelta(minutes=rng.randint(90, 360))
        for k in range(size):
            created = t0 + timedelta(minutes=rng.randint(0, 80))
            extra = rng.choice(["This started right after the {l} {e}.", "It began following the {e} at {l}.",
                                "Seems related to the {e} in {l}."]).format(l=loc, e=event)
            make(v, created, "Resolved", loc=loc, app=app, impact=rng.choice([1, impact]), urgency=rng.choice(["Blocked", "Degraded"]),
                 extra=extra, dup="HG-%03d" % (g + 1), resolved_at=fixed_at)

    # 2b. Recurring problems: the same fault returns on the same service and site, and gets the same temporary fix
    for sub, sop, app, loc, period, count in SERIES:
        v = next(x for x in V if x["sub"] == sub and x["sop"] == sop)
        for k in range(count):
            created = now - timedelta(days=3 + k * period + rng.uniform(-1, 1))
            created = created.replace(hour=rng.randint(8, 17), minute=rng.randint(0, 59), second=rng.randint(0, 59))
            extra = rng.choice(["This has happened before.", "Same problem as last time.",
                                "This keeps coming back every few days.", "It was fixed earlier but it is back again."])
            make(v, created, "Resolved", loc=loc, app=app, extra=extra)

    # 3. Open tickets for the analyst queue
    for _ in range(30):
        make(rng.choice(V), now - timedelta(minutes=rng.randint(20, 1800)), rng.choice(["Open", "Open", "In Progress"]))
    # 4. One live incident: several users reporting the same VPN certificate failure
    for k in range(5):
        make(V[0], now - timedelta(minutes=rng.randint(10, 110)), "Open", loc="Hyderabad", app="GlobalProtect VPN",
             impact=rng.choice([1, 2]), urgency="Blocked",
             extra="This started right after the Hyderabad certificate rotation.", dup="HG-OPEN")

    rows.sort(key=lambda r: r["created_at"])
    for i, r in enumerate(rows):
        r["ticket_id"] = "TKT-%05d" % (10001 + i)
        r["created_at"] = r["created_at"].strftime(FMT)
        if r["resolved_at"]:
            r["resolved_at"] = r["resolved_at"].strftime(FMT)
    with open(os.path.join(DATA, "tickets.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(DATA, "sops.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sop_id", "title", "category", "risk_level", "requires_approval", "steps", "source"])
        for s in SOPS:
            w.writerow([s[0], s[1], s[2], s[3], s[4], " | ".join(s[6]), "synthetic-generated"])
    print("Wrote %d tickets and %d SOPs to %s" % (len(rows), len(SOPS), DATA))


if __name__ == "__main__":
    main()
