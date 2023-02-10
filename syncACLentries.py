#!/usr/bin/env python3.9

import requests
import json
import os
import sys
import copy
import argparse

#####################################################################
######################### GLOBAL VARIABLES ##########################
try:
  FASTLY_API_HEADERS = {'Fastly-Key': os.environ["FASTLY_API_TOKEN"]}
  debug = False
except KeyError as e:
  print("Missing FASTLY_API_TOKEN environment variable. Exiting...")
  sys.exit(1)
#####################################################################
#####################################################################

# Function to check status code of 'requests' call, and print error msg and returned text if not 200
def check_r(r, errormsg):
  if r.status_code != 200:
    print(errormsg)
    print(r.text)
    sys.exit(1)

def getSVCver(svcID):
  r = requests.get(f"https://api.fastly.com/service/{svcID}/version", headers=FASTLY_API_HEADERS)
  check_r(r, f"Getting the svc versions of service {svcID} failed. Exiting...")
  for version in r.json():
    if version["active"]:
      return version["number"]
  print(f"Couldn't find active service version number for service {svcID}. Exiting...")
  sys.exit(1)

def getACLidFromName(svcID, svcVER, aclNAME):
  r = requests.get(f"https://api.fastly.com/service/{svcID}/version/{svcVER}/acl/{aclNAME}", headers=FASTLY_API_HEADERS)
  check_r(r, f"Getting ACL ID for {aclNAME} from service {svcID} version {svcVER} failed. Exiting...")
  return r.json()['id']

def getACLentries(svcID, svcVER, aclNAME, aclENTRIES):
  # Get active version
  if not svcVER:
    svcVER = getSVCver(svcID)
  
  # Get ACL ID
  aclID = getACLidFromName(svcID, svcVER, aclNAME)
  
  # Build a dict of existing ACL entries
  r = requests.get(f"https://api.fastly.com/service/{svcID}/acl/{aclID}"
                   f"/entries?direction=ascend&page=1&per_page=1000&sort=created",
                   headers=FASTLY_API_HEADERS)
  check_r(r, f"Getting ACL entries for ACL {aclNAME} from service {svcID} failed. Exiting...")

  for acl in r.json():

    # Normalize data
    if not acl["subnet"]:
      acl["subnet"] = 32
    if not acl["comment"]:
      acl["comment"] = "None"

    aclIP = acl["ip"]
    aclSUBNET = acl["subnet"]
    aclCOMMENT = acl["comment"]

    if aclIP in aclENTRIES:
      # IP is already in aclENTRIES, so check if duplicate and/or correct
      if aclENTRIES[aclIP]["subnet"] == aclSUBNET and aclENTRIES[aclIP]["comment"] == aclCOMMENT:
        # 100% duplicate entry, skip
        continue
      if aclENTRIES[aclIP]["subnet"] == "None" and aclSUBNET != "None":
        aclENTRIES[aclIP]["subnet"] = aclSUBNET
      if aclENTRIES[aclIP]["comment"] == "None" and aclCOMMENT != "None":
        aclENTRIES[aclIP]["comment"] = aclCOMMENT
    else:
      # IP is not in aclENTRIES yet, so add it
      aclENTRIES[aclIP] = {"subnet": aclSUBNET, "comment": aclCOMMENT}

def updateACLentries(svcID, svcVER, aclNAME, aclENTRIES):
  # Get active version
  if not svcVER:
    svcVER = getSVCver(svcID)

  # Get ACL ID
  aclID = getACLidFromName(svcID, svcVER, aclNAME)

  # Build a dict of existing ACL entries (to later use to determine whether to use 'create' or 'update' for ACL entries)
  r = requests.get(f"https://api.fastly.com/service/{svcID}/acl/{aclID}"
                   f"/entries?direction=ascend&page=1&per_page=1000&sort=created",
                   headers=FASTLY_API_HEADERS)
  check_r(r, f"Getting ACL entries for ACL {aclNAME} from service {svcID} failed. Exiting...")

  existingACLEntries = {}
  for acl in r.json():
    existingACLEntries[acl["ip"]] = {
      "id": acl["id"],
      "subnet": acl["subnet"],
      "comment": acl["comment"]
    }

  payload = []
  for IP in aclENTRIES:
    if IP in existingACLEntries:
      # First check if it's already correct (so we can skip it)
      if aclENTRIES[IP]["subnet"] == existingACLEntries[IP]["subnet"] and \
              aclENTRIES[IP]["comment"] == existingACLEntries[IP]["comment"]:
        # Existing entry matches entry to add, so skip
        continue
      payload.append({"op": "update",
                      "ip": IP,
                      "subnet": aclENTRIES[IP]["subnet"],
                      "comment": aclENTRIES[IP]["comment"],
                      "id": existingACLEntries[IP]["id"]})
    else:
      payload.append({"op": "create",
                      "ip": IP,
                      "subnet": aclENTRIES[IP]["subnet"],
                      "comment": aclENTRIES[IP]["comment"]})

  if debug:
    print(f"Creating/updating {len(payload)} ACLs in svc {svcID}:{svcVER}:{aclNAME}...")

  if len(payload) == 0:
    return

  if debug:
    print(f"Payload to be applied:")
    for i in payload:
      print(i)

  patchheaders = copy.copy(FASTLY_API_HEADERS)
  patchheaders['Content-type'] = "application/json"
  #patchdata = json.dumps({"entries": payload})
  r = requests.patch(f"https://api.fastly.com/service/{svcID}/acl/{aclID}/entries",
                     headers=patchheaders,
                     data={"entries": payload})
  check_r(r, f"Updating ACL entries for ACL {aclNAME} from service {svcID} failed. Exiting...")
  print(f"Result of PATCH API call: {r.text}")


# MAIN
if __name__ == '__main__':

  # Set up argument parser
  parser = argparse.ArgumentParser()
  parser.add_argument('--svcid', nargs='+', required=True, help='One or more serviceIDs')
  parser.add_argument('--aclname', type=str, required=True, help='The ACL name to be synced')
  parser.add_argument('--verbose', type=str, default=False, help='Print progress info')
  args = parser.parse_args()
  debug = args.verbose

  entries = {}
  # Get the ACL's entries from all the services into 1 dictionary
  for svc in args.svcid:
    print(f"Getting '{args.aclname}' ACL entries from service '{svc}'...")
    #getACLentries(svc, "", args.aclname, entries)

  # And now use that consolidated dictionary to update the ACL in all the services
  for svc in args.svcid:
    print(f"Updating '{args.aclname}' ACL entries for service '{svc}'...")
    #updateACLentries(svc, "", args.aclname, entries)

  print("\nDone.\n")

  sys.exit(0)