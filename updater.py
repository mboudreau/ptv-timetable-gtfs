devid = ""
key = ""
endpoint = "http://timetableapi.ptv.vic.gov.au"

# pip install requests dataset pymysql
# pypy updater.py 1> op.log 2> error.log

#TODO
#
# time/date calculation sometimes fails and provides a negative number - needs some refactoring
# Routes with the same name should be one entry in the routes table
# There are trips with no stopping pattern. These should be logged and not included in db
# stations with same lat/lng should be merged
# Unused stops purged

from Queue import LifoQueue
from threading import Thread, Lock
import requests
from hashlib import sha1
from datetime import datetime
import hmac
import time
import dataset
import sys, traceback
from sys import stderr
from sqlalchemy.exc import IntegrityError

db = dataset.connect('mysql://root@localhost/gtfs2')
#db = dataset.connect('sqlite:///gtfs.db')

db_stops = db.create_table("stops", "stop_id", "String")
#dict(stop_id, stop_name, stop_lat, stop_lon)
db_routes = db.create_table("routes", "route_id" ,"String")
#dict(route_id, route_long_name, route_type)
db_trips =db.create_table("trips", "trip_id" ,"String")
#dict(route_id, service_id, trip_id)
db_stop_times = db.create_table("stop_times")
#dict(trip_id, arrival_time, departure_time, stop_id, stop_sequence)
db_calendar_dates = db.create_table("calendar_dates", "service_id", "String")
#dict(service_id, date, exception_type)

print "DB created"
sys.stdout.flush()
mutex = Lock()

db_mutex = Lock() # work around for Commands out of sync; you can't run this command now

def signURL(url, keyid, devid):
	if "?" in url:
		url = url+"&devid=" + devid
	else:
		url = url +"?devid=" + devid
	dhash = hmac.new(keyid, url, sha1).hexdigest().rstrip('\n').upper()
	return url + "&signature=" + dhash

def do_stuff(q):
  while True:
    (function, args ) = q.get()
    try:
    	print "start function " + str(function) + " - " + str(args)
    	function(*args)
    	print "end function " + str(function) + " - " + str(args)
    except:
    	stderr.write( "== ERROR with == " )
    	stderr.write( str(args) )
    	stderr.write(traceback.format_exc())
    	stderr.write("========" )
    	sys.stderr.flush()
    finally:
    	q.task_done()
 
q = LifoQueue(maxsize=0)
num_threads = 12
 
 #pre load the queue with sleep commands to stagger the startup
for i in range(num_threads):
  q.put((time.sleep, (i* 60,)))

for i in range(num_threads):
  worker = Thread(target=do_stuff, args=(q,))
  worker.setDaemon(True)
  worker.start()

 
#for x in range(100):
#  q.put(x)

 

""" 
0 train
1 tram
2 bus
3 vline train and coach
4 bus
"""

#ptv transport types to ptv tranport type codes
modes = {
	"train" : "0",
	"tram" : "1",
	"bus" : "2",
#	"vline" : "3" ,
	"nightbus" : "4"
}

#mapping of ptv transport types to googles
transport_types = {
	"train":"1",
	"tram":"0",
	"bus":"3",
	"nightrider":"3"
}

"""lines/mode/0"""
stops = set()
runs = {"0": set(), "1": set(), "2": set(), "4": set()}
routes = []
days = set()
trips = set()

def getLines():
	for mode in modes.values():
		print "Starting Mode " + mode
		sys.stdout.flush()
		r = requests.get(endpoint + signURL( "/v2/lines/mode/" + mode, key, devid))
		for line in r.json():
			q.put((getStops,(mode, str(line["line_id"]))))

"""/v2/mode/%@/line/%@/stops-for-line"""
def getStops(mode, line_id):
	print "Starting line " + line_id
	sys.stdout.flush()
	r = requests.get(endpoint + signURL( "/v2/mode/"+mode+"/line/"+ line_id+"/stops-for-line" , key, devid))
	for stop in r.json():
		if stop["stop_id"] not in stops:
			mutex.acquire()
			try:
				stops.add(stop["stop_id"])
			finally:
				mutex.release()

			db_stops.insert(dict(stop_id=stop["stop_id"], stop_name=stop['location_name'], stop_lat=stop["lat"], stop_lon=stop["lon"]))

			q.put((getNextDeparts,(mode,str(stop["stop_id"]))))

""" /v2/mode/%@/stop/%@/departures/by-destination/limit/%@ """
def getNextDeparts(mode, stop_id):
	mutex.acquire()
	skip = False
	try:
		if stop_id in stops:
			skip = True
		else:
			stops.add(stop_id)
	finally:
		mutex.release()
	if skip == False:
		print "Starting next departs " + stop_id
		sys.stdout.flush()
		r = requests.get(endpoint + signURL( "/v2/mode/"+mode+"/stop/"+stop_id+"/departures/by-destination/limit/10000" , key, devid))

		for departure in r.json()["values"]:
			caldate = departure["time_timetable_utc"].split("T")[0].replace("-","")
			if caldate not in days:
				db_mutex.acquire()
				try:
					db_calendar_dates.upsert(dict(service_id=caldate, date=caldate, exception_type="1"), ['service_id']) 
				finally:
					db_mutex.release()
				days.add(caldate)
			if "M" + mode + "R"+str(departure["run"]["run_id"])+ "D" +caldate not in trips:
				trips.add("M" + mode + "R"+str(departure["run"]["run_id"])+ "D" +caldate)
				try:
					if (departure['platform']['direction']['line']['line_name'] not in routes):
						routes.append(departure['platform']['direction']['line']['line_name'])
						routeIndex = "RI" + str(routes.index(departure['platform']['direction']['line']['line_name']))
						db_routes.insert(dict(route_id=routeIndex, route_long_name=departure['platform']['direction']['line']['line_name'], route_type=transport_types[departure['run']["transport_type"]]), ["route_id"])

					routeIndex = "RI" + str(routes.index(departure['platform']['direction']['line']['line_name']))
					db_trips.insert(dict(route_id=routeIndex, service_id=caldate, trip_id="M" + mode + "R"+str(departure["run"]["run_id"])+ "D" +caldate) ,['trip_id']);
					
					if str(departure["run"]["run_id"])+ "D" +caldate not in runs[mode]:
						q.put((getStoppingPattern,(mode, stop_id, str(departure["run"]["run_id"]), caldate)))
				except IntegrityError:
					pass


""" /v2/mode/%@/run/%@/stop/%@/stopping-pattern """

def getStoppingPattern(mode, stop_id, run_id, caldate):
	mutex.acquire()
	skip = False
	try:
		if run_id+ "D" + caldate in runs[mode]:
			skip = True
		else:
			runs[mode].add(run_id+ "D" +caldate)
	finally:
		mutex.release()
	if skip == False:
		print "Starting stopping pattern " + "M" + mode  + "R" + run_id
		sys.stdout.flush()
		r = requests.get(endpoint + signURL( "/v2/mode/"+mode+"/run/"+run_id+"/stop/"+stop_id+"/stopping-pattern" , key, devid))
		first = True
		sequence=1
		for pattern in r.json()["values"]:
			if first:
				orig_date = datetime.strptime(pattern["time_timetable_utc"], "%Y-%m-%dT%H:%M:%SZ")
				first = False
				midnight_date = orig_date.replace(hour=0, minute=0, second=0, microsecond=0)
			thisstop_date = datetime.strptime(pattern["time_timetable_utc"], "%Y-%m-%dT%H:%M:%SZ")
			c = thisstop_date - midnight_date
			time = "%02d:%02d:00" % ((c.days*24) + c.seconds//3600, (c.seconds//60)%60)
			db_stop_times.insert(dict(trip_id="M" + mode + "R"+run_id+ "D" +caldate, arrival_time=time, departure_time=time, stop_id=pattern["platform"]["stop"]["stop_id"], stop_sequence=sequence))
			sequence += 1



getLines() 
q.join()
