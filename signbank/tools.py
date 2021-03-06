from settings.base import WSGI_FILE, WRITABLE_FOLDER, GLOSS_VIDEO_DIRECTORY
import os
import shutil
from HTMLParser import HTMLParser
from zipfile import ZipFile
from datetime import datetime, date
import json
import re

import signbank.settings
from signbank.settings.base import LANGUAGE_CODE
from django.utils.translation import override

from signbank.dictionary.models import *
from django.utils.dateformat import format

from django.core.urlresolvers import reverse

def save_media(source_folder,goal_folder,gloss,extension):
        
    #Add a dot before the extension if needed
    if extension[0] != '.':
        extension = '.' + extension

    #Figure out some names
    annotation_id = gloss.annotation_idgloss
    pk = str(gloss.pk)
    destination_folder = goal_folder+annotation_id[:2]+'/'

    #Create the necessary subfolder if needed
    if not os.path.isdir(destination_folder):
        os.mkdir(destination_folder)

    #Move the file
    source = source_folder+annotation_id+extension
    goal = destination_folder+annotation_id+'-'+pk+extension

    if os.path.isfile(goal):
        overwritten = True
    else:
        overwritten = False

    try:
        shutil.copy(source,goal)
        was_allowed = True
    except IOError:
        was_allowed = False

    os.remove(source)

    return overwritten,was_allowed

def unescape(string):

    return HTMLParser().unescape(string)

class MachineValueNotFoundError(Exception):
    pass

def compare_valuedict_to_gloss(valuedict,gloss):
    """Takes a dict of arbitrary key-value pairs, and compares them to a gloss"""

    #Create an overview of all fields, sorted by their human name
    with override(LANGUAGE_CODE):
        fields = {field.verbose_name: field for field in gloss._meta.fields}

        differences = []

        #Go through all values in the value dict, looking for differences with the gloss
        for human_key, new_human_value in valuedict.items():

            new_human_value = new_human_value.strip()

            #This fixes a casing problem that sometimes shows up
            if human_key.lower() == 'id gloss':
                human_key = 'ID Gloss'

            #If these are not fields, but relations to other parts of the database, go look for differenes elsewhere
            if human_key == 'Keywords':

                current_keyword_string = str(', '.join([translation.translation.text.encode('utf-8') for translation in gloss.translation_set.all()]))

                if current_keyword_string != new_human_value:
                    differences.append({'pk':gloss.pk,
                                        'idgloss':gloss.idgloss,
                                        'machine_key':human_key,
                                        'human_key':human_key,
                                        'original_machine_value':current_keyword_string,
                                        'original_human_value':current_keyword_string,
                                        'new_machine_value':new_human_value,
                                        'new_human_value':new_human_value})

            #If not, find the matching field in the gloss, and remember its 'real' name
            try:
                field = fields[human_key]
                machine_key = field.name
            except KeyError:
                continue

            #Try to translate the value to machine values if needed
            if len(field.choices) > 0:
                human_to_machine_values = {human_value: machine_value for machine_value, human_value in field.choices}

                try:
                    new_machine_value = human_to_machine_values[new_human_value]
                except KeyError:

                    #If you can't find a corresponding human value, maybe it's empty
                    if new_human_value in ['',' ']:
                        new_human_value = 'None'
                        new_machine_value = None

                    #If not, stop trying
                    else:
                        raise MachineValueNotFoundError('At '+gloss.idgloss+' ('+str(gloss.pk)+'), could not find option '+str(new_human_value)+' for '+human_key)

            #Do something special for integers and booleans
            elif field.__class__.__name__ == 'IntegerField':

                try:
                    new_machine_value = int(new_human_value)
                except ValueError:
                    new_human_value = 'None'
                    new_machine_value = None
            elif field.__class__.__name__ == 'NullBooleanField':

                if new_human_value in ['True','true']:
                    new_machine_value = True
                else:
                    new_machine_value = False

            #If all the above does not apply, this is a None value or plain text
            else:
                if new_human_value == 'None':
                    new_machine_value = None
                else:
                    new_machine_value = new_human_value

            #Try to translate the key to machine keys if possible
            try:
                original_machine_value = getattr(gloss,machine_key)
            except KeyError:
                continue

            #Translate back the machine value from the gloss
            try:
                original_human_value = dict(field.choices)[original_machine_value]
            except KeyError:
                original_human_value = original_machine_value

            #Remove any weird char
            try:
                new_machine_value = unescape(new_machine_value)
                new_human_value = unescape(new_human_value)
            except TypeError:
                pass

            #Check for change, and save your findings if there is one
            if original_machine_value != new_machine_value:
                differences.append({'pk':gloss.pk,
                                    'idgloss':gloss.idgloss,
                                    'machine_key':machine_key,
                                    'human_key':human_key,
                                    'original_machine_value':original_machine_value,
                                    'original_human_value':original_human_value,
                                    'new_machine_value':new_machine_value,
                                    'new_human_value':new_human_value})

    return differences

def reload_signbank(request=None):
    """Functions to clear the cache of Apache, also works as view"""

    #Refresh the wsgi script
    os.utime(WSGI_FILE,None)

    #If this is an HTTP request, give an HTTP response
    if request != None:

        from django.template import RequestContext
        from django.shortcuts import render_to_response

        return render_to_response('reload_signbank.html',context_instance=RequestContext(request))

def get_static_urls_of_files_in_writable_folder(root_folder,since_timestamp=0):

    full_root_path = signbank.settings.server_specific.WRITABLE_FOLDER+root_folder+'/'
    static_urls = {}

    for subfolder_name in os.listdir(full_root_path):
        for filename in os.listdir(full_root_path+subfolder_name):

            if os.path.getmtime(full_root_path+subfolder_name+'/'+filename) > since_timestamp:
                res = re.search(r'(.+)\.[^\.]*', filename)

                try:
                    gloss_id = res.group(1)
                except AttributeError:
                    continue

                static_urls[gloss_id] = reverse('dictionary:protected_media', args=[''])+root_folder+'/'+subfolder_name+'/'+filename

    return static_urls

def get_gloss_data(since_timestamp=0):

    glosses = Gloss.objects.all()
    gloss_data = {}
    for gloss in glosses:
        if int(format(gloss.lastUpdated, 'U')) > since_timestamp:
            gloss_data[gloss.pk] = gloss.get_fields_dict()

    return gloss_data

def create_zip_with_json_files(data_per_file,output_path):

    """Creates a zip file filled with the output of the functions supplied.

    Data should either be a json string or a list, which will be transformed to json."""

    INDENTATION_CHARS = 4

    zip = ZipFile(output_path,'w')

    for filename, data in data_per_file.items():

        if isinstance(data,list) or isinstance(data,dict):
            output = json.dumps(data,indent=INDENTATION_CHARS)
            zip.writestr(filename+'.json',output)

def get_deleted_gloss_or_media_data(item_type,since_timestamp):

    result = []
    deletion_date_range = [datetime.fromtimestamp(since_timestamp),date.today()]

    for deleted_gloss_or_media in DeletedGlossOrMedia.objects.filter(deletion_date__range=deletion_date_range,
                                                            item_type=item_type):
        if item_type == 'gloss':
            result.append((deleted_gloss_or_media.old_pk, deleted_gloss_or_media.idgloss))
        else:
            result.append(deleted_gloss_or_media.old_pk)

    return result


def generate_still_image(gloss_prefix, vfile_location, vfile_name):
    try:
        from CNGT_scripts.python.extractMiddleFrame import MiddleFrameExtracter
        from signbank.settings.server_specific import FFMPEG_PROGRAM, TMP_DIR
        from signbank.settings.base import GLOSS_IMAGE_DIRECTORY

        # Extract frames (incl. middle)
        extracter = MiddleFrameExtracter([vfile_location+vfile_name], TMP_DIR + os.sep + "signbank-extractMiddleFrame",
                                         FFMPEG_PROGRAM, True)
        output_dirs = extracter.run()

        # Copy video still to the correct location
        for dir in output_dirs:
            for filename in os.listdir(dir):
                if filename.replace('.png', '.mp4') == vfile_name:
                    destination = WRITABLE_FOLDER + GLOSS_IMAGE_DIRECTORY + os.sep + gloss_prefix
                    still_goal_location = destination + os.sep + filename
                    if not os.path.isdir(destination):
                        os.makedirs(destination, 0o750)
                    elif os.path.isfile(still_goal_location):
                        # Make a backup
                        backup_id = 1
                        made_backup = False

                        while not made_backup:

                            if not os.path.isfile(still_goal_location + '_' + str(backup_id)):
                                os.rename(still_goal_location, still_goal_location + '_' + str(backup_id))
                                made_backup = True
                            else:
                                backup_id += 1
                    shutil.copy(dir + os.sep + filename, destination + os.sep + filename)
            shutil.rmtree(dir)
    except ImportError as i:
        print(i.message)
    except IOError as io:
        print(io.message)
