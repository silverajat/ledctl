import sys,os
d=os.path.dirname(os.path.abspath(sys.argv[1]))
html=open(sys.argv[1]).read()
man='{"name":"ledctl","short_name":"ledctl","start_url":"/","display":"standalone","background_color":"#0b0b0f","theme_color":"#0b0b0f","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml"}]}'
icon='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#0b0b0f"/><circle cx="50" cy="50" r="24" fill="#ff9a3c"/><circle cx="50" cy="50" r="34" fill="none" stroke="#ff9a3c" stroke-opacity=".35" stroke-width="5"/></svg>'
out='// generated from page.html by mkpage.py\n#pragma once\n'
for n,s in [('PAGE_HTML',html),('MANIFEST_JSON',man),('ICON_SVG',icon)]:
    out+=f'const char {n}[] PROGMEM = R"ledctl_raw({s})ledctl_raw";\n'
open(os.path.join(d,'page.h'),'w').write(out)
