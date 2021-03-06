#!/usr/bin/env python2
from __future__ import print_function, division
import os, sys, remote, requests, zipfile, csv, psycopg2, collections, math
from urlparse import urljoin

exclude_usa = False

def stream_summary_files(start_url):
    '''
    '''
    index = requests.get(start_url).json()
    
    for collection in index['collections']['global'].values():
        collection_url = collection['url']
        print('Downloading', collection_url, '...', file=sys.stderr)

        collection = zipfile.ZipFile(remote.RemoteFileObject(collection_url))
        namelist = list()
        
        for name in collection.namelist():
            # CSV file paths look like "summary/{iso}/{etc}.csv"
            iso_a2 = os.path.relpath(name, 'summary').split(os.path.sep)[0].upper()
            _, ext = os.path.splitext(name)
        
            if (iso_a2 == '..') or (ext != '.csv'):
                continue # Need a CSV file for the requested ISO code.
            
            if iso_a2 == 'US' and exclude_usa:
                continue

            namelist.append(name)
    
        for (index, name) in enumerate(sorted(namelist)):
            print(u'Reading from {} ({}/{})...'.format(name, index+1, len(namelist)), file=sys.stderr)
            # CSV file paths look like "summary/{iso}/{etc}.csv"
            iso_a2 = os.path.relpath(name, 'summary').split(os.path.sep)[0].upper()

            for row in csv.DictReader(collection.open(name)):
                lon, lat = float(row['lon']), float(row['lat'])
                count, geom_wkt = int(row['count']), row['area']
                yield (iso_a2, count, lon, lat, 0.1)

start_url = 'https://results.openaddresses.io/index.json'
summaries = collections.defaultdict(lambda: collections.defaultdict(int))

for (iso_a2, count, lon, lat, size) in stream_summary_files(start_url):
    summaries[iso_a2][(lon, lat, size)] += count

with psycopg2.connect(os.environ['DATABASE_URL']) as conn:
    with conn.cursor() as db:
        for (index, iso_a2) in enumerate(sorted(summaries.keys())):
            print('Counting up {} ({}/{})...'.format(iso_a2, index+1, len(summaries)), file=sys.stderr)
            
            counts = list()
            
            db.execute('''SELECT boxes.lon, boxes.lat, boxes.size, gpwv4_2015.population
                          FROM boxes, gpwv4_2015, areas
                          WHERE gpwv4_2015.iso_a2 = %s
                            AND areas.iso_a2 = gpwv4_2015.iso_a2
                            AND boxes.id = gpwv4_2015.box_id
                            AND boxes.size = 0.1
                            AND ST_Intersects(boxes.geom, areas.geom)''',
                       (iso_a2, ))
            
            for (lon, lat, size, population) in db.fetchall():
                if (lon, lat, size) not in summaries[iso_a2]:
                    continue
                
                count = summaries[iso_a2][(lon, lat, size)]
                if population:
                    counts.append(count/population)
                else:
                    counts.append(0)
            
            if not counts:
                continue

            #
            # Calculate real statistics
            #
            counts.sort()
            median = counts[len(counts) // 2]
            mean = sum([c/len(counts) for c in counts])
            deviations = [(c - mean) ** 2 for c in counts]
            variance = sum([d/len(deviations) for d in deviations])
            std_dev = math.sqrt(variance)
            
            print(iso_a2, '- mean:', mean, 'median:', median, 'stddev:', std_dev, 'min:', min(counts), 'max:', max(counts))
            
            #
            # Real statistics have some wacky outliers, so cut off the top and
            # bottom 2% and try again. This attenuates the deviations a bit.
            #
            cutoff1 = int(len(counts) * 0.02)
            cutoff2 = int(len(counts) * 0.98)
            _counts = counts[cutoff1:cutoff2]
            _median = _counts[len(_counts) // 2]
            _mean = sum([c/len(_counts) for c in _counts])
            _deviations = [(c - _mean) ** 2 for c in _counts]
            _variance = sum([d/len(_deviations) for d in _deviations])
            _std_dev = math.sqrt(_variance)
            
            print(iso_a2, '- _mean:', _mean, '_median:', _median, '_stddev:', _std_dev, '_min:', min(_counts), '_max:', max(_counts))
            
            db.execute('''UPDATE areas SET cpp_min = %s, cpp_max = %s, cpp_avg = %s,
                          cpp_med = %s, cpp_stddev = %s WHERE iso_a2 = %s''',
                       (min(counts), max(counts), _mean, _median, _std_dev, iso_a2))
