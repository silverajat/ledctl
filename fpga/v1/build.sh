set -e
yowasp-yosys -q -p "read_verilog ledctl_top.v; synth_gowin -top ledctl_top -json top.json" 
yowasp-nextpnr-himbaechel-gowin --json top.json --write pnr.json --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=ledctl_v1.cst --freq 27 2>&1 | grep -E "Max frequency|Info: Device utilisation|LUT4|DFF|ERROR|Warning: .*(timing|freq)" | head -12
gowin_pack -d GW2A-18C -o ledctl_v1.fs pnr.json
ls -la ledctl_v1.fs
