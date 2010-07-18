
all:

server:
	google_appengine/dev_appserver.py .

update:
	google_appengine/appcfg.py update .

push:
	git push origin master
