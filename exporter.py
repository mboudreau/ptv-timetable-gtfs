import dataset
db = dataset.connect('sqlite:///gtfs.db')

stop_query = db.query('SELECT stop_id,stop_name,stop_lat,stop_lon FROM stops')

routes_query = db.query('SELECT route_id, "0" as agency_id ,"" as route_short_name,route_long_name,route_type FROM routes')

trips_query = db.query('SELECT route_id,service_id,trip_id FROM trips')

stop_times_query = db.query('SELECT trip_id, arrival_time, departure_time, stop_id, stop_sequence FROM stop_times')

calendar_dates_query = db.query('SELECT service_id, date, exception_type FROM calendar_dates')


dataset.freeze(stop_query,format='csv',filename='stops.txt')
dataset.freeze(routes_query,format='csv',filename='routes.txt')
dataset.freeze(trips_query,format='csv',filename='trips.txt')
dataset.freeze(stop_times_query,format='csv',filename='stop_times.txt')
dataset.freeze(calendar_dates_query,format='csv',filename='calendar_dates.txt')