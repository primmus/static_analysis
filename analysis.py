#!/usr/bin/env python
# -*- coding: utf-8 -*-
# (c) 2017, Lionel PRAT <lionel.prat9@gmail.com>
# Analysis by clamav extraction and yara rules
# All rights reserved.
import logging
import pydot
import hashlib
import shutil
import os
import json, pprint
import tempfile
import yara
import re
import errno
from datetime import datetime, timedelta
import subprocess
import sys, getopt
import collections
import zlib

## file[path], direcory_extract[path], graph[bool]
#verify clamscan present, or verify ENV CLAMSCAN_PATH
#verify option else display menu
#TODO: verify other md5 present in json result and find in directory

#########################################################################################################
##### USE MSO FILE EXTRACT because clamav don't uncompress activemime
########### FUNCTION ORIGIN: https://github.com/decalage2/oletools/blob/master/oletools/olevba.py
########### Author: Philippe Lagadec - http://www.decalage.info
########### License: BSD, see source code in https://github.com/decalage2/oletools/
MSO_ACTIVEMIME_HEADER = b'ActiveMime'

def is_mso_file(data):
    """
    Check if the provided data is the content of a MSO/ActiveMime file, such as
    the ones created by Outlook in some cases, or Word/Excel when saving a
    file with the MHTML format or the Word 2003 XML format.
    This function only checks the ActiveMime magic at the beginning of data.
    :param data: bytes string, MSO/ActiveMime file content
    :return: bool, True if the file is MSO, False otherwise
    """
    return data.startswith(MSO_ACTIVEMIME_HEADER)


# regex to find zlib block headers, starting with byte 0x78 = 'x'
re_zlib_header = re.compile(r'x')


def mso_file_extract(data):
    """
    Extract the data stored into a MSO/ActiveMime file, such as
    the ones created by Outlook in some cases, or Word/Excel when saving a
    file with the MHTML format or the Word 2003 XML format.
    :param data: bytes string, MSO/ActiveMime file content
    :return: bytes string, extracted data (uncompressed)
    raise a MsoExtractionError if the data cannot be extracted
    """
    # check the magic:
    assert is_mso_file(data)

    # In all the samples seen so far, Word always uses an offset of 0x32,
    # and Excel 0x22A. But we read the offset from the header to be more
    # generic.
    offsets = [0x32, 0x22A]

    # First, attempt to get the compressed data offset from the header
    # According to my tests, it should be an unsigned 16 bits integer,
    # at offset 0x1E (little endian) + add 46:
    try:
        offset = struct.unpack_from('<H', data, offset=0x1E)[0] + 46
        offsets.insert(0, offset)  # insert at beginning of offsets
    except:
        pass
    # now try offsets
    for start in offsets:
        try:
            extracted_data = zlib.decompress(data[start:])
            return extracted_data
        except zlib.error as exc:
            pass
    # None of the guessed offsets worked, let's try brute-forcing by looking
    # for potential zlib-compressed blocks starting with 0x78:
    for match in re_zlib_header.finditer(data):
        start = match.start()
        try:
            extracted_data = zlib.decompress(data[start:])
            return extracted_data
        except zlib.error as exc:
            pass
############ END OF FUNCTION ORIGIN: https://github.com/decalage2/oletools/blob/master/oletools/olevba.py
#########################################################################################################
def usage():
    print "Usage: analysis.py [-c /usr/local/bin/clamscan] [-d /tmp/extract_emmbedded] [-s /tmp/graph.png] [-j /tmp/result.json] [-m coef_path] [-g] [-v] -f path_filename -y yara_rules_path/\n\n"
    print "\t -h/--help : for help to use\n"
    print "\t -f/--filename= : path of filename to analysis\n"
    print "\t -y/--yara_rules_path= : path of filename to analysis\n"
    print "\t -c/--clamscan_path= : path of binary clamscan [>=0.99.3]\n"
    print "\t -m/--coef_path= : path of coef config file\n"
    print "\t -d/--directory_tmp= : path of directory to extract emmbedded file(s)\n"
    print "\t -j/--json_save= : path filename where save json result (JSON)\n"
    print "\t -g/--graph : generate graphe of analyz\n"
    print "\t -s/--save_graph= : path filename where save graph (PNG)\n"
    print "\t -v/--verbose= : verbose mode\n"
    print "\t example: analysis.py -f /home/analyz/strange/invoice.rtf -y /home/analyz/yara_rules/ -g\n"

#https://stackoverflow.com/questions/3431825/generating-an-md5-checksum-of-a-file
def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

#https://stackoverflow.com/questions/6027558/flatten-nested-python-dictionaries-compressing-keys
def flatten(d, parent_key='', sep='_'):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, collections.MutableMapping):
            items.extend(flatten(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
    
#extract pattern info: URI, IP, ...
def extract_info(pathfile,pat):
    find = []
    with open(pathfile, 'r') as content_file:
        content = content_file.read()
        for k, v in pat.items():
            ret = re.findall(v,content)
            retl = [each for each in ret if len(each) >0]
            for item in retl:
                tmp = {}
                tmp[k] = str(item)
                find.append(tmp)
    return find
#check key exist element in key dict
def checkdict(nested_dict,path):
    cour=nested_dict
    for pk in path:
        if type(pk) is int:
            cour=cour[pk]
        elif pk in cour:
            cour=cour[pk]
        else:
            return False
    return True
    
#read element in key dict
def readdict(nested_dict,path):
    cour=nested_dict
    for pk in path:
        if type(pk) is int:
            cour=cour[pk]
        elif pk in cour:
            cour=cour[pk]
        else:
            return False
    return cour

#extract dict level key/value by path
def dict_extract_path(nested_dict,path):
    edict={}
    flat_info = {}
    cour=nested_dict
    for pk in path:
        if type(pk) is int:
            cour=cour[pk]
        elif pk in cour:
            cour=cour[pk]
        else:
            return edict
    for k, v in cour.items():
        if u"ContainedObjects" != k:
            if type(v) is str:
                edict[k.encode('utf8')]=v
            elif type(v) is int:
                edict[k.encode('utf8')+"_int"]=v
            elif type(v) is bool:
                edict[k.encode('utf8')+"_bool"]=v
            elif type(v) is unicode:
                edict[k.encode('utf8')]=v.encode('utf8')
            elif type(v) is dict:
                tmp = flatten(v,k.encode('utf8'))
                flat_info.update(tmp)
            elif type(v) is list:
                edict[k] = str(v)
    for kr,vr in flat_info.items():
        if type(vr) is list:
            if kr not in edict:
                edict[kr] = str(vr)
            else:
                edict[kr] = edict[kr] + "||--||" + str(vr)
        elif type(vr) is bool:
            edict[kr+"_bool"] = vr
        elif type(vr) is int:
            edict[kr+"_int"] = vr
        else:
            if kr not in edict:
                edict[kr] = str(vr.encode('utf8'))
            else:
                edict[kr] = edict[kr] + "||--||" + str(vr)
    return edict

#add element in key dict
def adddict(nested_dict,k,v,path,overwrite=False):
    cour=nested_dict
    for pk in path:
        if type(pk) is int:
            cour=cour[pk]
        elif pk in cour:
            cour=cour[pk]
        else:
            return False
    if k in cour:
        if type(cour[k]) is list:
            if type(v) is list:
                if overwrite:
                    cour[k]=v
                else:
                    for elemv in v:
                        if not elemv in cour[k]:
                            cour[k].append(elemv)
                #cour[k] = list(set(cour[k]))
            else:
                if not v in cour[k]:
                    cour[k].append(v)
        elif k == 'RiskScore':
            if cour[k] < v:
                cour[k]=v
        else:
            if not cour[k] == v:
               if overwrite:
                   cour[k] += v
               else:
                   cour[k] += "||||" + v
    else:
        if k == u'ContainedObjects':
            cour[k]=[v]
        else:
            cour[k]=v
    return nested_dict

#modify element in key dict
def moddict(nested_dict,v,path):
    cour=nested_dict
    for pk in path:
        if type(pk) is int:
            cour=cour[pk]
        elif pk in cour:
            cour=cour[pk]
        else:
            return False
    cour=v
    return nested_dict

                    
#function to find md5 in result clamav
def getpath(nested_dict, value, prepath=()):
    resultx = []
    for k, v in nested_dict.items():
        path = prepath + (k,)
        #print str(k) +  " == " + str(value) + " in " + str(path)
        if type(v) is list:
            count = 0
            for elem in v:
                if type(elem) is dict:
                    ret = getpath(elem, value, path + (count,)) # recursive call
                    resultx = ret + resultx
                    #if p is not None:
                    #    return p
                count = count + 1
        elif type(v) is dict: # v is a dict
            ret = getpath(v, value, path) # recursive call
            resultx = ret + resultx
            #if p is not None:
                #return p
        elif k == u'FileMD5' and v == value: # found value
            resultx.append(path)
    return resultx

def findLogPath(serr,directory_tmp,path_find):
    file_parent = ""
    #re.findall(r'(/tmp/tmpMYwPhO/clamav-[0-9a-f]+.tmp/.*)\s+.*\n(.*\n){1,100}.*/tmp/tmpMYwPhO/clamav-9ad6c389cad6fe266160874482974c84.tmp/clamav-542c546718bca7c316f719ea416f6a6e',content,re.MULTILINE)
    r=re.findall(r'(' + directory_tmp + "/clamav-[0-9a-f]+.tmp/.*)\s+.*\n(.*\n){1,100}.*" + path_find,serr,re.MULTILINE)
    #print "R: " + str(r)
    if r:
        file_parent = r[0][0]
    #find md5 file parent
    return file_parent

def check_all_score(nested_dict):
    scores = {}
    for k, v in nested_dict.items():
        if type(v) is list and k == u"Yara":
            for elem in v:
                if type(elem) is dict:
                    for kx, vx in elem.items():
                        scores[kx] = vx['score']
        if type(v) is list and k == u"ContainedObjects":
            for elem in v:
                if type(elem) is dict:
                    ret = check_all_score(elem) # recursive call
                    scores.update(ret)
        elif type(v) is dict: # v is a dict
            ret = check_all_score(v) # recursive call
            scores.update(ret)
    return scores

def remove_double(nested_dict):
    list_md5 = []
    remove_count = []
    for k, v in nested_dict.items():
        if type(v) is list and k == u"ContainedObjects":
            count = 0
            for elem in v:
                if type(elem) is dict and u'FileMD5' in elem:
                    if elem[u'FileMD5'] in list_md5:
                        #remove
                        remove_count.append(count)
                    else:
                        list_md5.append(elem[u'FileMD5']) 
                count += 1
            for index in sorted(remove_count, key=int, reverse=True):
                v.pop(index)
            for elem in v:
                if type(elem) is dict and u'ContainedObjects' in elem:
                    remove_double(elem)
        elif type(v) is dict: # v is a dict
            remove_double(v) # recursive call
  
def scan_json(filename, cl_parent, cl_type, patterndb, var_dynamic, extract_var_global, yara_RC, score_max, md5_file, externals_var_extra={}):
    #find size file 
    size_file = os.path.getsize(filename)
    #extract info
    ext_info = extract_info(filename,patterndb)
    extract_var_local = {}
    for elemx in ext_info:
        for kx, vx in elemx.items():
            if kx not in extract_var_local:
                extract_var_local["extract_local_"+kx] = vx
            elif vx not in extract_var_local[kx]:
                extract_var_local["extract_local_"+kx] = extract_var_local[kx] + "||--||" + vx
            if kx not in extract_var_global:
                extract_var_global["extract_global_"+kx] = vx
            elif vx not in extract_var_global[kx]:
                extract_var_global["extract_global_"+kx] = extract_var_global[kx] + "||--||" + vx
    #yara check
    externals_var = {'FileParentType': cl_parent, 'FileType': "CL_TYPE_" + cl_type, 'FileSize': int(size_file), 'FileMD5': md5_file.encode('utf8'), 'PathFile': filename}
    if externals_var_extra:
        externals_var.update(externals_var_extra)
    externals_var.update(var_dynamic)
    #add extinfo in var_dyn
    externals_var.update(extract_var_local)
    externals_var.update(extract_var_global)
    ret_yara = yara_RC.match(filename, externals=externals_var, timeout=120)
    detect_yara_rule = []
    detect_yara_score = 0
    detect_yara_strings = ext_info
    for match in ret_yara:
        if match.meta['weight'] > 0:
            detect_yara_rule.append({match.rule: {'description': match.meta['description'], 'score': match.meta['weight']}})
            if match.meta['weight'] > detect_yara_score:
                detect_yara_score = match.meta['weight']
                if detect_yara_score > score_max:
                    score_max = detect_yara_score
            #detect_yara_strings += match.strings
            #detect_yara_strings = list(set(detect_yara_strings))
            if 'var_match' in match.meta:
                var_dynamic[str(match.meta['var_match'])] = True
        elif 'var_match' in match.meta:
            var_dynamic[str(match.meta['var_match'])] = True
    result_file = { u'FileParentType': cl_parent, u'FileType': u"CL_TYPE_" + unicode(cl_type, "utf-8"), u'FileSize': int(size_file), u'FileMD5': md5_file, u'PathFile': [unicode(filename, "utf-8")],  u'RiskScore': detect_yara_score, u'Yara': detect_yara_rule, u'ExtractInfo': detect_yara_strings, u'ContainedObjects': []}
    return score_max, var_dynamic, extract_var_global, result_file
    
def clamscan(clamav_path, directory_tmp, filename_path, yara_RC, patterndb, coef, verbose):
    #add time in external variable yara for special ch\teck
    now=datetime.now()
    dd=datetime(int(now.strftime('%Y')),int(now.strftime('%m')),int(now.strftime('%d')))+timedelta(days=-7)
    tnow7=dd.strftime("%s000")
    result_extract = {}
    coefx = 1 
    print "Extract emmbedded file(s) with clamav..."
    #create empty file for no check sig on file
    emptyrule_path = tempfile.gettempdir() + '/emptyrule.yar'
    if not os.path.isfile(emptyrule_path):
        open(emptyrule_path, 'a').close()
    (working_dir, filename) = os.path.split(filename_path)
    new_env = dict(os.environ)
    args = [clamav_path, '--gen-json', '--debug', '--leave-temps', '--normalize=no', '--tempdir=' + directory_tmp, '-d', emptyrule_path, filename]
    proc = subprocess.Popen(args, env=new_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=working_dir)
    output, serr = proc.communicate()
    print "Analyz result..."
    #run command problem
    if verbose:
        print serr
    if proc.returncode:
        print "Error: clamscan could not process the file.\n"
        sys.exit()
    #run command OK
    else:
        #find json file -- > json written to: tmp5//clamav-07c46ccfca138bfce61564c552931476.tmp
        root_type = "UNKNOWN" 
        score_max = 0
        var_dynamic = {}
        extract_var_global = {}
        m = re.search('json written to:\s+(.+)\n', serr)
        json_find = False
        json_file = ""
        if m:
            json_file = m.group(1)
            print "Find resultat in json file:" + json_file + "..."
            if os.path.isfile(json_file):
                with open(json_file) as data_file:
                    try:    
                        result_extract = json.load(data_file)
                    except:
                        print "Error to parse json result..."
        var_dynamic['now_7_int'] = int(tnow7)
        if result_extract:
            json_find = True
            remove_double(result_extract)
        else:
            #analyz debug information for find external variable for yara
            regexp_bool = re.compile(r'_bool$')
            regexp_int = re.compile(r'_int$')
            #Put serr (clamav debug) in external variable if json not detected
            var_dynamic['serr'] = serr
            pdf_analyz = { 'cli_pdf: %%EOF not found': u'PDFStats_NoEOF_bool', 'cli_pdf: encrypted pdf found': u'PDFStats_Encrypted_bool', 'cli_pdf: did not find valid xref': u'PDFStats_NoXREF_bool', 'cli_pdf: startxref not found': u'PDFStats_NoXREF_bool', 'cli_pdf: bad pdf version:': u'PDFStats_BadVersion_bool', 'cli_pdf: no PDF- header found': u'PDFStats_BadHeaderPosition_bool', 'cli_pdf: bad format object': u'PDFStats_InvalidObjectCount_int'}
            for ka,va in pdf_analyz.items():
                if ka in serr:
                    if regexp_bool.search(va):
                        var_dynamic[va] = True
                    elif regexp_int.search(va):
                        var_dynamic[va] = 1
                    else:
                        var_dynamic[va] = "True"
            md5_file = unicode(md5(filename_path), "utf-8")
            size_file = os.path.getsize(filename_path)
            #LibClamAV debug: Recognized RTF file
            type_file = "UNKNOWN"
            m = re.search('LibClamAV debug:\s+Recognized\s+(\S+)\s+', serr) #LibClamAV debug: Recognized RTF file
            if m:
                type_file = m.group(1)
                root_type = type_file
        #extract info
        ext_info = extract_info(filename_path,patterndb)
        extract_var_local = {}
        for elemx in ext_info:
            for kx, vx in elemx.items():
                if kx not in extract_var_local:
                    extract_var_local["extract_local_"+kx] = vx
                elif vx not in extract_var_local[kx]:
                    extract_var_local["extract_local_"+kx] = extract_var_local[kx] + "||--||" + vx
                if kx not in extract_var_global:
                    extract_var_global["extract_global_"+kx] = vx
                elif vx not in extract_var_global[kx]:
                    extract_var_global["extract_global_"+kx] = extract_var_global[kx] + "||--||" + vx
        #verify yara rules
        if json_find:
            externals_var=dict_extract_path(result_extract,())
        else:
            externals_var = {'RootFileType': "CL_TYPE_" + type_file, 'FileType': "CL_TYPE_" + type_file, 'FileSize': int(size_file), 'FileMD5': md5_file.encode('utf8')}
        #add var_dynamic in var ext
        externals_var.update(var_dynamic)
        #add extinfo in var_dyn
        externals_var.update(extract_var_local)
        externals_var.update(extract_var_global)
        ret_yara = yara_RC.match(filename_path, externals=externals_var, timeout=120)
        detect_yara_rule = []
        detect_yara_score = 0
        detect_yara_strings = ext_info
        for match in ret_yara:
            if match.meta['weight'] > 0:
                detect_yara_rule.append({match.rule: {'description': match.meta['description'], 'score': match.meta['weight']}})
                if match.meta['weight'] > detect_yara_score:
                    detect_yara_score = match.meta['weight']
                    if detect_yara_score > score_max:
                       score_max = detect_yara_score
                #detect_yara_strings += match.strings
                #detect_yara_strings = list(set(detect_yara_strings))
                if 'var_match' in match.meta:
                    var_dynamic[str(match.meta['var_match'])] = True
            elif 'var_match' in match.meta:
                var_dynamic[str(match.meta['var_match'])] = True
        if json_find:
            reta = adddict(result_extract,u'RiskScore',detect_yara_score,())
            reta = adddict(result_extract,u'Yara',detect_yara_rule,())
            reta = adddict(result_extract,u'ExtractInfo',detect_yara_strings,())
        else:
            result_extract = { u'RootFileType': u"CL_TYPE_" + unicode(type_file, "utf-8"), u'FileType': u"CL_TYPE_" + unicode(type_file, "utf-8"), u'FileSize': int(size_file), u'FileMD5': md5_file, u'RiskScore': detect_yara_score, u'Yara': detect_yara_rule, u'ExtractInfo': detect_yara_strings, u'ContainedObjects': []}
        #reanalyse log clamav for create JSON information
        level_cour = 0
        tempdir_cour = ""
        cl_parent = result_extract[u'RootFileType'].encode('utf8')
        cl_parentmd5 = result_extract[u'FileMD5']
        temp_json = {} # 'temp_dir': { 'CL_PARENT': clparent, 'LEVEL': level }
        all_md5 = {}
        regexp_dir = re.compile(directory_tmp+r'\/clamav-[a-z0-9]{32}.tmp\/[a-zA-Z0-9\/\._-]+')
        regexp_dirx = re.compile(directory_tmp+r'\/clamav-[a-z0-9]{32}.tmp')
        regexp_file = re.compile(directory_tmp+r'\/clamav-[a-z0-9]{32}.tmp([^\/]|$|\n)')        
        #TODO: ADD SPECIAL PROCESS FOR CL_TYPE_MHTML->CL_TYPE_BINARY_DATA(ActiveMime)
        for linex in serr.splitlines():
           #parse result clamav for make json result
           matchx = regexp_dir.search(linex)
           matchf = regexp_file.search(linex)
           if matchx or matchf:
               #new file
               filex=""
               if matchx:
                   filex=matchx.group(0)
               elif matchf:
                   tmpf = regexp_dirx.search(matchf.group(0))
                   print "MATCHF:"+str(matchf.group(0))
                   if tmpf:
                       filex=tmpf.group(0)
                   else:
                       continue
                   print "MATCHF OK:"+str(filex)
               if os.path.isfile(filex) and json_file != filex:
                   #file exist
                   #check md5sum
                   md5_file = unicode(md5(filex), "utf-8")
                   nopresent = True
                   #check if dir exist in temp_json?
                   fpresent = False
                   if matchx:
                       (dirtmp, filenamex) = os.path.split(filex)
                       dirx = regexp_dirx.search(dirtmp)
                       if dirx:
                           dirx=dirx.group(0)
                       else:
                           dirx=dirtmp
                       if md5_file in all_md5 and all_md5[md5_file ] == 1:
                           fpresent = True
                   else:
                       #same level
                       dirx = tempdir_cour
                       if md5_file in all_md5:
                           continue
                   type_file = "UNKNOWN"
                   externals_var_extra={}
                   #activemime ret
                   ret_analyz=""
                   if json_find:
                       #find type in json
                       find_type = getpath(result_extract, md5_file)
                       if find_type:
                           find_type = find_type[0] + (u'FileType',)
                           type_file_tmp = readdict(result_extract,find_type)
                           if type_file_tmp:
                               type_file = type_file_tmp
                           #extract extra info of clamav
                           externals_var_extra=dict_extract_path(result_extract,find_type[0][0:len(find_type[0])-1])
                   else:
                       r=re.compile(filex+"(.*\n){0,5}LibClamAV debug:\s+Recognized\s+(?P<type>\S+)", re.MULTILINE)
                       for m in r.finditer(serr):
                           ret=m.groupdict() 
                           if ret['type']:
                               type_file = ret['type']
                   swf_add_info = {}
                   if 'SWF' in type_file and 'SWF: File attributes:' in serr:
                       #extract SWF file attributes
                       r=re.compile("SWF: File attributes:(?:.*\n){1}(LibClamAV debug:\s+\*\s+[^\n]+\n){1,10}", re.MULTILINE)
                       aswf=r.search(serr)
                       swf_add_info = {u'SWF_attributes': {}}
                       if aswf:
                           r=re.compile("LibClamAV debug:\s+\*\s+(?P<type>[^\n]+)")
                           print "SWG G0:" + str(aswf.group(0))
                           for m in r.finditer(aswf.group(0)):
                               retx=m.groupdict() 
                               print "SWG RET:" + str(retx)
                               if retx['type']:
                                   swf_add_info[u'SWF_attributes'][retx['type'].replace(" ", "_")]=True
                                   externals_var_extra[u'swf_attributes_'+retx['type'].replace(" ", "_").replace(".", "").lower()+'_bool']=True
                   if 'CL_TYPE_MHTML' in serr and not md5_file in all_md5 and (type_file == "UNKNOWN" or type_file == "CL_TYPE_BINARY_DATA"):
                       with open(filex, 'rb') as fx:
                           content = fx.read()
                           if content[:len(MSO_ACTIVEMIME_HEADER)].startswith(MSO_ACTIVEMIME_HEADER):
                               #uncompress
                               uc_activemime=mso_file_extract(content)
                               #write uncompress
                               with open(filex+'_activemime', 'wr+') as f:
                                   f.write(uc_activemime)
                       if os.path.isfile(filex+'_activemime'):
                           #run analyz clamav
                           print "\tAnalyz interne activemime on " + str(md5_file) + "..."
                           ret_analyz=clamscan(clamav_path, directory_tmp, filex+'_activemime', yara_RC, patterndb, {}, verbose)
                           print "\tEnd of analyz interne activemime!"
                   if not dirx in temp_json:
                       #new dir -> new level OR first file!
                       level_cour += 1
                       tempdir_cour = dirx
                       temp_json[dirx] = {"level": level_cour, "cl_parent": cl_parent, "files": []}
                       find_md5 = getpath(result_extract, cl_parentmd5)
                       list_PType = ""
                       if find_md5:
                           temp_json[dirx]['find_md5'] = find_md5
                           for x in xrange(len(find_md5[0])): #keep courant field
                               fpmd5 = find_md5[0][0:x]
                               fpmd5 = fpmd5 + (u'FileType',)
                               type_parent = readdict(result_extract,fpmd5)
                               if type_parent:
                                   list_PType += "->" + type_parent
                           temp_json[dirx]['cl_parent'] = list_PType
                       #scan yara and make json
                       score_max, var_dynamic, extract_var_global, ret = scan_json(filex, temp_json[dirx]["cl_parent"], type_file, patterndb, var_dynamic, extract_var_global, yara_RC, score_max, md5_file, externals_var_extra)
                       temp_json[dirx]['files'].append(md5_file)
                       if matchx:
                           all_md5[md5_file] = 0
                       else:
                           all_md5[md5_file] = 1
                   elif tempdir_cour == dirx:
                       #new file in same level
                       if not md5_file in temp_json[dirx]['files']:
                           score_max, var_dynamic, extract_var_global, ret = scan_json(filex, temp_json[dirx]["cl_parent"], type_file, patterndb, var_dynamic, extract_var_global, yara_RC, score_max, md5_file, externals_var_extra)
                           temp_json[dirx]['files'].append(md5_file)
                           if matchx:
                               all_md5[md5_file] = 0
                           else:
                               all_md5[md5_file] = 1
                       else:
                           nopresent = False
                   else:
                       #new file in old level
                       level_cour = temp_json[dirx]["level"]
                       cl_parent = temp_json[dirx]["cl_parent"]
                       tempdir_cour = dirx
                       if not md5_file in temp_json[dirx]['files']:
                           score_max, var_dynamic, extract_var_global, ret = scan_json(filex, temp_json[dirx]["cl_parent"], type_file, patterndb, var_dynamic, extract_var_global, yara_RC, score_max, md5_file, externals_var_extra)
                           temp_json[dirx]['files'].append(md5_file)
                           if matchx:
                               all_md5[md5_file] = 0
                           else:
                               all_md5[md5_file] = 1
                       else:
                           nopresent = False
                   if nopresent:
                       if swf_add_info:
                           ret.update(swf_add_info)
                       if ret_analyz:
                           #remove key global
                           ret_analyz.pop(u'RootFileType', None)
                           if ret_analyz[u'GlobalRiskScore'] > score_max:
                               score_max = ret_analyz[u'GlobalRiskScore']
                           ret_analyz.pop(u'GlobalRiskScore', None)
                           ret_analyz.pop(u'GlobalRiskScoreCoef', None)
                           ret[u'ContainedObjects'].append(ret_analyz)
                       if json_find:
                           find_md5 = getpath(result_extract, md5_file)
                           if find_md5:
                               for pmd5 in find_md5:
                                       reta = adddict(result_extract,u'FileParentType',ret[u'FileParentType'],pmd5[0:len(pmd5)-1],fpresent)
                                       reta = adddict(result_extract,u'PathFile',ret[u'PathFile'],pmd5[0:len(pmd5)-1],fpresent)
                                       reta = adddict(result_extract,u'RiskScore',ret[u'RiskScore'],pmd5[0:len(pmd5)-1],fpresent)
                                       reta = adddict(result_extract,u'Yara',ret[u'Yara'],pmd5[0:len(pmd5)-1],fpresent)
                                       reta = adddict(result_extract,u'ExtractInfo',ret[u'ExtractInfo'],pmd5[0:len(pmd5)-1],fpresent)
                                       if swf_add_info:
                                           reta = adddict(result_extract,u'SWF_attributes',ret[u'SWF_attributes'],pmd5[0:len(pmd5)-1],fpresent)
                                       if ret_analyz:
                                           print "RET ANALYZ -- ADD1"
                                           pp = pprint.PrettyPrinter(indent=4)
                                           pp.pprint(ret)
                                           reta = adddict(result_extract,u'ContainedObjects',ret_analyz,pmd5[0:len(pmd5)-1],fpresent)
                           else:
                               #md5 not present in json
                               for pmd5 in temp_json[dirx]['find_md5']:
                                   reta = adddict(result_extract,u'ContainedObjects',ret,pmd5[0:len(pmd5)-1])
                       else:
                           if level_cour == 1:
                               result_extract["ContainedObjects"].append(ret)
                           else:
                               for pmd5 in temp_json[dirx]['find_md5']:
                                   reta = adddict(result_extract,u'ContainedObjects',ret,pmd5[0:len(pmd5)-1])
                   cl_parentmd5 = md5_file 
        #verify json with md5 not find in debug log
        if json_find:
            fpresent = True
            md5_list = []
            #parse json result and find md5 not in debug log
            md5_free = find_md5free(result_extract)
            #find file with md5 in tmp folder
            for root, directories, filenames in os.walk(directory_tmp):
                for filename in filenames:
                    md5_file = unicode(md5(os.path.join(root, filename)), "utf-8")
                    if md5_file in md5_free and md5_file not in md5_list:
                        md5_list.append(md5_file)
                        #analyz
                        type_file = "UNKNOWN"
                        list_PType = ""
                        #find type in json
                        find_type = getpath(result_extract, md5_file)
                        if find_type:
                            for pmd5 in find_type:
                                #find parent type
                                for x in xrange(len(pmd5)-1):
                                    fpmd5 = pmd5[0:x]
                                    fpmd5 = fpmd5 + (u'FileType',)
                                    type_parent = readdict(result_extract,fpmd5)
                                    if type_parent:
                                        list_PType += "->" + type_parent
                            find_typex = find_type[0] + (u'FileType',)
                            type_file_tmp = readdict(result_extract,find_typex)
                            if type_file_tmp:
                                type_file = type_file_tmp
                            #extract extra info of clamav
                            externals_var_extra=dict_extract_path(result_extract,find_type[0][0:len(find_type[0])-1])
                        score_max, var_dynamic, extract_var_global, ret = scan_json(os.path.join(root, filename), list_PType, type_file, patterndb, var_dynamic, extract_var_global, yara_RC, score_max, md5_file, externals_var_extra)
                        for pmd5 in find_type:
                            reta = adddict(result_extract,u'FileParentType',ret[u'FileParentType'],pmd5[0:len(pmd5)-1],fpresent)
                            reta = adddict(result_extract,u'PathFile',ret[u'PathFile'],pmd5[0:len(pmd5)-1],fpresent)
                            reta = adddict(result_extract,u'RiskScore',ret[u'RiskScore'],pmd5[0:len(pmd5)-1],fpresent)
                            reta = adddict(result_extract,u'Yara',ret[u'Yara'],pmd5[0:len(pmd5)-1],fpresent)
                            reta = adddict(result_extract,u'ExtractInfo',ret[u'ExtractInfo'],pmd5[0:len(pmd5)-1],fpresent)
        #actualiz score max
        result_extract[u'GlobalRiskScore'] = score_max
        result_extract[u'GlobalRiskScoreCoef'] = coefx
        #calcul globalriskscore with coef
        if coef:
            scores=check_all_score(result_extract)
            #remove max
            for k, v in scores.items():
                if v == score_max:
                    scores.pop(k, None)
                    break
            #calcul coef
            for k, v in scores.items():
                if str(v) in coef:
                    coefx += coef[str(v)]
            score_max_coef = int(round(score_max * coefx))
            if score_max_coef > 10:
                score_max_coef = 10
            result_extract[u'GlobalRiskScore'] = score_max_coef
            result_extract[u'GlobalRiskScoreCoef'] = coefx
    print "Phase one finish!\n"
    return result_extract

def find_md5free(nested_dict):
    md5_list = []
    if u'FileMD5' in nested_dict and not u'RiskScore' in nested_dict:
        md5_list.append(nested_dict[u'FileMD5'])
    if "ContainedObjects" in nested_dict:
        for elem in nested_dict["ContainedObjects"]:
            if type(elem) is dict:
               ret = find_md5free(elem) # recursive call
               md5_list += ret
    return md5_list
        
def json2dot(nested_dict, dangerous_score, name_cour, name_parent):
    dot_content = ""
    if u'FileMD5' in nested_dict and not u'RootFileType' in nested_dict:
        #create DOT line
        color="green"
        #if u'GlobalRiskScore' in nested_dict and nested_dict[u'GlobalRiskScore'] >= dangerous_score:
        if nested_dict[u'RiskScore'] >= dangerous_score:
            color="red"
        dot_content += name_cour + ' [shape=record, label="{{' + nested_dict[u'FileMD5'].encode('utf8') + '|' + str(nested_dict[u'RiskScore']) + '}|' + nested_dict[u'FileType'].encode('utf8') + '}", color=' + color + '];\n'    
        if nested_dict[u'Yara']:
            dot_content += name_cour + '_info [label="' + str(nested_dict[u'Yara']).replace('"', '').replace("'", '').encode('utf8') + '", color=blue];\n'    
        # create link
        if color == 'red':
            dot_content += name_parent + ' -> ' + name_cour + ' [color=red];\n'
        else:
            dot_content += name_parent + ' -> ' + name_cour + ';\n'
        if nested_dict[u'Yara']:
            dot_content += name_cour + ' -- ' + name_cour + '_info [style=dotted];\n'
    if "ContainedObjects" in nested_dict:
        #extract info object
        count = 0
        for elem in nested_dict["ContainedObjects"]:
            if type(elem) is dict:
                ret = json2dot(elem, dangerous_score, name_cour+'_'+str(count), name_cour) # recursive call
                dot_content += ret
            count += 1
    return dot_content

def create_graph(filename, result_extract, verbose, path_write_png='/tmp/analysis_result.png', dangerous_score=5):
    #create DOT
    dot_content = 'digraph Analysis {\nratio=auto;\npage="20,47";\n'
    color="green"
    if result_extract[u'GlobalRiskScore'] >= dangerous_score:
        color="red"
    dot_content += 'R_0 [shape=record, label="{{' + os.path.basename(filename) + '|' + str(result_extract[u'GlobalRiskScore']) + '|' + 'Coef:' + str(result_extract[u'GlobalRiskScoreCoef']) + '}|' + result_extract[u'RootFileType'].encode('utf8') + '}", color=' + color + '];\n'
    if result_extract[u'Yara']:
            dot_content += 'R_0_info [label="' + str(result_extract[u'Yara']).replace('"', '').replace("'", '').encode('utf8') + '", color=blue];\n' 
            dot_content += 'R_0 -- R_0_info [style=dotted];\n'
    dot_content += json2dot(result_extract, dangerous_score, 'R_0', 'R_0')
    dot_content += '}'
    if verbose:
        print dot_content
    #convert dot to png
    (graph,) = pydot.graph_from_dot_data(dot_content)
    graph.write_png(path_write_png)
    
def yara_compile(yara_rules_path, ext_var={}):
    try:
        rules = yara.compile(filepaths=yara_rules_path, externals=ext_var)
    except Exception as e:
        loop = True
        count = 0
        error = str(e)
        while loop:
            r=re.findall(r'undefined identifier \"(\S+)\"',error)
            count += 1
            if count > 300:
                print "Error: lot of Errors > 300 -- Yara rules compilations =>" + error
                sys.exit()
            if r:
               if "_bool" in str(r[0]):
                   ext_var[str(r[0])]=False
               elif "_int" in str(r[0]):
                   ext_var[str(r[0])]=-1
               else:
                   ext_var[str(r[0])]=""
               try:
                   rules = yara.compile(filepaths=yara_rules_path, externals=ext_var)
                   loop = False
               except Exception as e:
                   error = str(e)
            else:
                print "Error: Yara rules compilations =>" + error
                sys.exit()
    return rules
    
def main(argv):
    print "Static analysis by clamav and yara rules -- Contact: lionel.prat9@gmail.com"
    clamav_path = "/usr/bin/clamscan"
    filename = ""
    directory_tmp = ""
    graph_file = ""
    json_file = ""
    yarapath = {}
    patterndb = {}
    coef = {}
    verbose = False
    make_graphe = False
    try:
        opts, args = getopt.getopt(argv, "hf:gc:d:y:s:j:p:m:v", ["help", "filename=", "graph", "clamscan_path=", "directory_tmp=", "yara_rules_path=", "save_graph=", "json_save=", "pattern=", "coef_path=", "verbose"])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            usage()
            sys.exit()
        elif opt in ("-g", "--graph"):
            make_graphe = True
        elif opt in ("-v", "--verbose"):
            verbose = True
        elif opt in ("-s", "--save_graph"):
            make_graphe = True
            (working_dir, filename) = os.path.split(arg)
            if os.path.isdir(working_dir):
                if os.path.splitext(arg)[1] != '.png':
                    arg += '.png'
                graph_file = arg
            else:
                print "Error: unuable to create directory: " + working_dir + ".\n"
                sys.exit()
        elif opt in ("-p", "--pattern"):
            #pattern load
            if not os.path.isfile(arg):
                print "Error: File: " + arg + " not exist.\n"
                usage()
                sys.exit()
            pattern_content = file(arg)
            for line in pattern_content:
                words = line.split('=>>')
                if words:
                    words[1] = words[1].replace("\n" , "")
                    patterndb[words[0]] = words[1]
        elif opt in ("-m", "--coef_path"):
            #coef load
            if not os.path.isfile(arg):
                print "Error: File: " + arg + " not exist.\n"
                usage()
                sys.exit()
            tmp_content = file(arg)
            for line in tmp_content:
                if '#' not in line and not '\n' == line:
                    words = line.split(':')
                    if words:
                        words[1] = words[1].replace("\n" , "")
                        coef[words[0]] = float(words[1])
        elif opt in ("-j", "--json_save"):
            (working_dirj, filenamej) = os.path.split(arg)
            if os.path.isdir(working_dirj):
                if os.path.splitext(arg)[1] != '.json':
                    arg += '.json'
                json_file = arg
            else:
                print "Error: unuable to create directory: " + working_dirj + ".\n"
                sys.exit()
        elif opt in ("-d", "--directory_tmp"):
            if not os.path.isdir(arg):
                #make directory
                try:
                    os.makedirs(arg)
                except OSError as e:
                    print "Error: unuable to make directory temp.\n"
                    sys.exit()
            else:
                #verify directory is empty
                #ask for remove
                confirm_rm = raw_input("Confirm remove all contained files in " + arg + ": Y/N ?").lower()
                if confirm_rm.startswith('y'):
                    shutil.rmtree(arg)
                    try:
                        os.makedirs(arg)
                    except OSError as e:
                        print "Error: unuable to make directory temp.\n"
                        sys.exit()
            directory_tmp = arg
        elif opt in ("-f", "--filename"):
            filename = arg
            #verify file exist
            if not os.path.isfile(filename):
                print "Error: File: " + arg + " not exist.\n"
                usage()
                sys.exit()
        elif opt in ("-y", "--yara_rules_path"):
            #verify file exist
            if os.path.isfile(arg):
                yarapath[str(os.path.basename(arg))] = str(arg)
            elif os.path.isdir(arg):
                for root, directories, filenames in os.walk(arg):
                    for filen in filenames:
                        yarapath[str(os.path.basename(filen))] = str(os.path.join(root, filen))
                if not yarapath:
                    print "Error: File(s) yara: " + arg + " not exist.\n"
                    usage()
                    sys.exit()
            else:
                print "Error: Yara rules path: " + arg + " not exist.\n"
                usage()
                sys.exit()
        elif opt in ("-c", "--clamscan_path"):
            clamav_path = arg
    #verify option need
    if not filename:
        usage()
        sys.exit()
    if not yarapath:
        usage()
        sys.exit()
    if not directory_tmp:
        directory_tmp = tempfile.mkdtemp()
        print "Create directory temp for emmbedded file: " + directory_tmp + "\n"
    #verify clamscan path exist
    if not os.path.isfile(clamav_path):
        print "Error: Binary clamscan [" + clamav_path + "] not exist.\n"
        usage()
        sys.exit()
    #compile yara rules
    #run clamscan on file with yara rule empty and option: --gen-json --debug -d empty_rule.yara --leave-temps --tempdir=$DIR_TEMP/
    yara_RC = yara_compile(yarapath)
    ret = clamscan(clamav_path, directory_tmp, filename,yara_RC, patterndb, coef, verbose)
    if json_file:
        with open(json_file, 'w') as fp:
            json.dump(ret, fp, sort_keys=True, indent=4)
        if verbose:
            pp = pprint.PrettyPrinter(indent=4)
            pp.pprint(ret)
    else:
        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(ret)
    if make_graphe:
        if graph_file:
            create_graph(filename,ret,verbose,graph_file)
        else:
            create_graph(filename,ret,verbose)
#parse log for find json file
#parse file for verify present in json, else parse log for find created

if __name__ == "__main__":
    main(sys.argv[1:])


