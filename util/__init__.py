import urllib
import requests
import datetime
import json
import xml.etree.ElementTree as ET
from pymongo import MongoClient

namespaces = {'cf': 'http://ns.medbiq.org/competencyframework/v1/',
			  'lom': 'http://ltsc.ieee.org/xsd/LOM'}

mongo = MongoClient()
db = mongo.compapp

# same comp could be in more than
# one place in this competency framework

# NOTE!! this system works right now because we only call this 
# after submitting an achieved statement.. if we break up getting 
# achieved statements, we will need to wipe out the statements in 
# the lrs to redemo
def setAchievement(theid, username, single=None):
	if single:
		comps = [single]
	else:
		comps = getAllUserComps(username)

	for comp in comps:
		if comp['entry'] == theid:
			comp['met'] = True
			comp['date'] = datetime.datetime.utcnow()
		
		# look through the whole framework for multiple
		# references to the same competency
		if comp.get('competencies', False):
			for c in comp['competencies']:
				setAchievement(theid, username, c)
		
		if rollup(comp):
			comp['met'] = True
			comp['date'] = datetime.datetime.utcnow()
		# only update if this is a root competency framework
		# single means it was called as a competency, not a root framework
		if not single:
			updateComp(comp, username)

def setAchievements(fwkcomp, ids, username, subfwk=False):
	for comp in fwkcomp['competencies']:
		if comp['entry'] in ids:
			comp['met'] = True
			comp['date'] = datetime.datetime.utcnow()

		if comp.get('competencies', False):
			for c in comp['competencies']:
				setAchievements(comp, ids, username, True)

		if rollup(comp):
			comp['met'] = True
			comp['date'] = datetime.datetime.utcnow()

	if not subfwk:
		if rollup(fwkcomp):
			fwkcomp['met'] = True
			fwkcomp['date'] = datetime.datetime.utcnow()
		updateComp(fwkcomp, username)

def rollup(comp):
	res = True
	if comp.get('competencies', False):
		for c in comp['competencies']:
			res = res & rollup(c)
	else:
		res = comp.get('met', False)
	return res

def updateCompFwkStatus(username, fwk, endpoint, auth):
	headers = {        
	            'Authorization': auth,
	            'content-type': 'application/json',        
	            'X-Experience-API-Version': '1.0.0'
	    		}

	# if framework comp ['met'] is true: return... everything is already met
	if fwk.get('met', False):
		return
	
	# request achieved with fwk uri and related activities
	mbox = db.users.find_one({"username":username})['email']
	actor = urllib.quote_plus(json.dumps({'mbox':mbox}))
	query_string = '?agent={0}&verb={1}&activity={2}&related_activities={3}'
	achievedverb = 'http://adlnet.gov/expapi/verbs/achieved'
	fwkentry = fwk['encodedentry']
	achievedquery = query_string.format(actor,achievedverb, fwkentry, 'true')

	get_resp = requests.get(endpoint + achievedquery , headers=headers, verify=False)
	
	if get_resp.status_code != 200:
		return
	achieved_ids = [s['object']['id'] for s in json.loads(get_resp.content)['statements']]
	# for each competency see if there is an id in the achieved array of statements that matches
	setAchievements(fwk, achieved_ids, username)

	updateCompetency(fwk, username, actor, headers, endpoint)

# now we wanna find 'passed' statements for each competency
def updateCompetency(fwkcomp, username, actor, headers, endpoint, subfwk=False):
	query_string = '?agent={0}&verb={1}&activity={2}&related_activities={3}'
	passedverb = 'http://adlnet.gov/expapi/verbs/passed'

	for comp in fwkcomp['competencies']:
		if comp.get('met', False):
			continue

		if comp.get('competencies', False):
			updateCompetency(comp, username, actor, headers, endpoint, True)
			if rollup(comp):
				comp['met'] = True
				comp['date'] = datetime.datetime.utcnow()	

		compentry = comp['encodedentry']
		passedquery = query_string.format(actor,passedverb, compentry, 'true')

		get_resp = requests.get(endpoint + passedquery , headers=headers, verify=False)
		if get_resp.status_code != 200:
			return

		if json.loads(get_resp.content).get('statements', False):
			comp['met'] = True
			comp['date'] = datetime.datetime.utcnow()

	# after all of that, do rollup.. see if all competencies in a framework are met, if so set fwk met
	if not subfwk:
		if rollup(fwkcomp):
			fwkcomp['met'] = True
			fwkcomp['date'] = datetime.datetime.utcnow()
		updateComp(fwkcomp, username)


def getAllUserComps(username):
	return db.usercomps.find({"username":username})	

def getUserCompsById(theid, username):
	mapfunc = """
	function(){
	  function dfs(node){
		  //emit(node.url, node.my_id)
		  if (node.entry === "%s") {
			return true;
		  }
		  else {
			for(var i in node.competencies) {
			  if (dfs(node.competencies[i])) {
				return true;
			  }
			}
		  }
		  return false;
	  }
	  if (this.username === "%s" && dfs(this)) {
		emit(this._id, this)
	  }
	}
	""" % (theid, username)
	#function(key, values){return JSON.stringify(values);}
	reducefunc = """
	function(key, values){return values;}
	"""

	result = db.usercomps.map_reduce(mapfunc, reducefunc, "myresults")
	return [d['value'] for d in result.find()]

def getMyComps(username):
	# get my comps from db.usercomps
	cursor = db.usercomps.find({"username":username}, {"_id":0})
	return [c for c in cursor]

def getAllSystemComps():
	return db.compfwk.find(fields={'_id': False})

def getComp(compuri, user=None):
	if user:
		mycomp = db.usercomps.find_one({"username":user, "entry":compuri}, {"_id":0})
		if mycomp:
			return mycomp
		else:
			comp = getComp(compuri)
			saveComp(comp, user)
			return comp
	
	fixed = compuri if not compuri.endswith('.xml') else compuri[:-4]
	comp = db.compfwk.find_one({"entry":compuri}, {"_id":0})
	if comp:
		return comp

	comp = parseCompetencies(compuri)

	if comp:
		saveComp(comp)
	
	return comp

def saveComp(comp, user=None):
	if user:
		comp['username'] = user
		db.usercomps.insert(comp)
	else:
		db.compfwk.insert(comp)

def updateComp(comp, username=None):
	if username:
		db.usercomps.update({"username":comp['username'], "entry":comp['entry']}, comp)
	else:
		db.compfwk.update({"entry":comp['entry']}, comp)

def getContentURLsFromLR(compuri):
	# get ids of documents w/ uri
	#https://node01.public.learningregistry.net/slice?any_tags=http://www.adlnet.gov/competency/scorm/choosing-an-lms/part3
	#####!!!!!!! hack
	compuri = compuri[:7] + 'www.' + compuri[7:]
	#####!!!!!!!! end hack
	url = "https://node01.public.learningregistry.net/slice?any_tags=%s" % compuri
	resp = requests.get(url)
	if resp.status_code != 200:
		return None
	lrresults = json.loads(resp.content)
	if lrresults['resultCount'] < 1:
		return None
	return [s['resource_data_description']['resource_locator'] for s in lrresults['documents']]

def parseCompetencies(uri):
	# cuz our xml is hosted with .xml right now
	uri = addXMLSuffix(uri)
	competencies = []
	try:
		res = requests.get(uri).text
	except Exception, e:
		return None
	fmwkxml = ET.fromstring(res)

	competencies = parse(fmwkxml)
	return competencies

def parse(xmlbit):
	obj = {}
	obj['type'] = 'framework' if 'CompetencyFramework' in xmlbit.tag else 'competency'
	obj['catalog'] = middleStuff(xmlbit)
	obj['entry'] = getEntry(xmlbit)
	obj['encodedentry'] = urllib.quote_plus(getEntry(xmlbit))
	obj['title'] = getTitle(xmlbit)
	obj['description'] = getDescription(xmlbit)
	obj['date'] = datetime.datetime.utcnow()
	for include in xmlbit.findall('cf:Includes', namespaces=namespaces):
		if not obj.get('competencies', False):
			obj['competencies'] = []
		url = addXMLSuffix(include.find('cf:Entry', namespaces=namespaces).text)
		nxt = ET.fromstring(requests.get(url).text)
		c = parse(nxt)
		obj['competencies'].append(c)
	return structure(xmlbit, obj)

def structure(fmwk, root):
	if not root.get('competencies', False):
		return root

	# if no relations, return comps
	for relation in fmwk.findall('cf:Relation', namespaces=namespaces):
		#get Reference1 object from comps, set Relationship attr to Reference2 entry
		ref1 = relation.find('cf:Reference1/cf:Entry', namespaces=namespaces).text
		rel = relation.find('cf:Relationship', namespaces=namespaces).text
		rel = rel[rel.rfind('#') + 1:]
		ref2 = relation.find('cf:Reference2/cf:Entry', namespaces=namespaces).text
		# if i'm referencing the framework as a relation... i can't do it here
		if ref1 == root['entry']:
			if not root.get(rel, False):
				root[rel] = []
			root[rel].append(ref2)
		else:
			for comp in root['competencies']:
				if ref1 == comp['entry']:
					if not comp.get(rel, False):
						comp[rel] = []
					comp[rel].append(ref2)
	return root

def addXMLSuffix(url):
	if url.endswith('.xml'):
		return url
	return url + ".xml"

def middleStuff(xml):
	return xml.find('lom:lom/lom:general/lom:identifier/lom:catalog', namespaces=namespaces).text

def getEntry(xml):
	return xml.find('lom:lom/lom:general/lom:identifier/lom:entry', namespaces=namespaces).text

def getTitle(xml):
	return xml.find('lom:lom/lom:general/lom:title/lom:string[@language="en"]', namespaces=namespaces).text

def getDescription(xml):
	return xml.find('lom:lom/lom:general/lom:description/lom:string[@language="en"]', namespaces=namespaces).text