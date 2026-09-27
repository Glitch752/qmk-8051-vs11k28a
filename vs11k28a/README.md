**what we have so far:**
- I used a random firmware update tool for a vs11k28a-based keyboard and captured the traffic with Wireshark. the capure is under `firmware_update.pcap`. My keyboard uses vid:pid `320f:5064` for the normal device and `0C45:7687` for the bootloader.
- I wrote a tiny script to dump the capture into a binary file - `firmware_update_payload.bin`.
- I'm currently working to decompile the firmware with Ghidra under `ghidra/`. I'm poor, so I'm not using agentic AI. The process is slow.
- I've written some scripts under `scripts/` to patch and write new firmware updates to the keyboard for testing. Some have been moderately successful.
- I've collected all the relevant datasheets and documentation I could find under `docs/`.
- 