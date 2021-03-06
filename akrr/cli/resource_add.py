"""
Python script for retrieving the 'resource' information from XDMoD and pre-generating
the default resource templates.
"""
import sys
import os
import getpass
import io
import re

from akrr import cfg
from akrr import log

from . import resource_deploy

###############################################################################
# VARIABLES
###############################################################################

# global variable to hold the script arguments
config_dir = cfg.cfg_dir
resources_dir = os.path.join(config_dir, 'resources')

resource_name = None
verbose = False
dry_run = False
minimalistic = False
no_ping = False

rsh = None
# variables for template with default values
info = None
ppn = None
remoteAccessNode = None
remoteAccessMethod = 'ssh'
remoteCopyMethod = 'scp'
sshUserName = None
sshPassword = None
sshPassword4thisSession = None
sshPrivateKeyFile = None
sshPrivateKeyPassword = None

networkScratch = None
localScratch = "/tmp"
akrrData = None
appKerDir = None
batchScheduler = None
batchJobHeaderTemplate = None

resource_cfg_filename = None


###############################################################################
# SCRIPT FUNCTIONS
###############################################################################

def retrieve_resources():
    """
    Retrieve the applicable contents of the `modw`.`resourcefact` table.
    :return: a tuple of strings containing the name of the resources.
    """
    con, cur = cfg.getXDDB()

    if con is None:
        # i.e. AKRR running without modw
        return tuple()

    cur.execute("SELECT `name`,`id` FROM `modw`.`resourcefact`")
    rows = cur.fetchall()

    log.debug("Retrieved %s Resource records...", len(rows) if rows else 0)

    return rows


def create_resource_config(file_path, queuing_system):
    """
    create resource file from template
    """

    with open(os.path.join(cfg.templates_dir, 'template.%s.conf' % queuing_system)) as fin:
        template = fin.read()
    fin.close()

    def update_template(s, variable, in_quotes=True):
        pattern = '^' + variable + '\s*=\s*.*$'
        replace = variable + ' = '
        value = globals()[variable]
        if value is None:
            replace += 'None'
        else:
            if in_quotes:
                replace += '"'
            replace += str(value)
            if in_quotes:
                replace += '"'
        out = []
        lines = s.splitlines()
        for line in lines:
            out.append(re.sub(pattern, replace, line))

        return "\n".join(out)

    template = update_template(template, 'ppn', in_quotes=False)
    for v in ['remoteAccessNode', 'remoteAccessMethod', 'remoteCopyMethod',
              'sshUserName', 'sshPassword', 'sshPrivateKeyFile', 'sshPrivateKeyPassword',
              'networkScratch', 'localScratch', 'akrrData', 'appKerDir', 'batchScheduler']:
        template = update_template(template, v)
    template += "\n\n"

    if not dry_run:
        with open(file_path, 'w') as fout:
            fout.write(template)
        fout.close()
    else:
        log.info(
            'Dry Run Mode: Would have written to: {}'
            'It content would be: {}'.format(file_path, template))


def generate_resource_config(resource_id, m_resource_name, queuing_system):
    from akrr.util.sql import cursor_execute
    log.info("Initiating %s at AKRR" % (m_resource_name,))

    if not dry_run:
        os.mkdir(os.path.join(resources_dir, m_resource_name), 0o700)

    file_path = os.path.abspath(os.path.join(resources_dir, m_resource_name, 'resource.conf'))
    global resource_cfg_filename
    resource_cfg_filename = file_path

    create_resource_config(file_path, queuing_system)

    # add entry to mod_appkernel.resource
    con_ak, cur_ak = cfg.getAKDB(True)

    cur_ak.execute('''SELECT * FROM resource WHERE nickname=%s''', (m_resource_name,))
    resource_in_ak_db = cur_ak.fetchall()
    if len(resource_in_ak_db) == 0:
        cursor_execute(
            cur_ak,
            "INSERT INTO resource (resource,nickname,description,enabled,visible,xdmod_resource_id)"
            "VALUES(%s,%s,%s,0,0,%s);",
            (m_resource_name, m_resource_name, m_resource_name, resource_id), dry_run)
        con_ak.commit()
    cur_ak.execute('''SELECT * FROM resource WHERE nickname=%s''', (m_resource_name,))
    if not dry_run:
        resource_in_ak_db = cur_ak.fetchall()
        resource_id_in_ak_db = resource_in_ak_db[0]['resource_id']
    else:
        resource_id_in_ak_db = 123
    # add entry to mod_akrr.resource
    db, cur = cfg.getDB(True)

    cur.execute('''SELECT * FROM resources WHERE name=%s''', (m_resource_name,))
    resource_in_db = cur.fetchall()
    if len(resource_in_db) == 0:
        cursor_execute(
            cur, '''INSERT INTO resources (id,xdmod_resource_id,name,enabled)
            VALUES(%s,%s,%s,%s);''',
            (resource_id_in_ak_db, resource_id, m_resource_name, 0), dry_run)
        db.commit()

        log.info("Resource configuration is in " + file_path)


def validate_resource_id(resource_id, resources):
    try:
        resource_id = int(resource_id)
    except (ValueError, TypeError):
        return False
    for _, resource_id2 in resources:
        if int(resource_id2) == int(resource_id):
            return True
    if resource_id == 0:
        return True
    return False


def validate_resource_name(m_resource_name):
    if m_resource_name.strip() == "":
        log.error("Bad name for resource, try a different name")
        return False
    # check config file presence
    file_path = os.path.abspath(os.path.join(resources_dir, m_resource_name))
    if os.path.exists(file_path):
        log.error(
            "Resource configuration directory (%s) for resource with name %s "
            "already present on file system, try a different name" % (file_path, m_resource_name,))
        return False

    # check the entry in mod_appkernel
    con_ak, cur_ak = cfg.getAKDB(True)

    cur_ak.execute('''SELECT * FROM resource WHERE nickname=%s''', (m_resource_name,))
    resource_in_ak_db = cur_ak.fetchall()
    if len(resource_in_ak_db) != 0:
        log.error(
            "Resource with name %s already present in mod_appkernel DB, try a different name" % (m_resource_name,))
        return False

    # check the entry in mod_akrr
    db, cur = cfg.getDB(True)

    cur.execute('''SELECT * FROM resources WHERE name=%s''', (m_resource_name,))
    resource_in_db = cur.fetchall()
    if len(resource_in_db) != 0:
        log.error("Resource with name %s already present in mod_akrr DB, try a different name" % (m_resource_name,))
        return False

    return True


def get_resource_name_by_id(resource_id, resources):
    resource_id = int(resource_id)

    for m_resource_name, resource_id2 in resources:
        if int(resource_id2) == int(resource_id):
            return m_resource_name
    return None


def validate_queuing_system(queuing_system):
    if queuing_system in ['slurm', 'pbs']:
        return True
    else:
        return False


def check_connection_to_resource():
    """check the connection to remote resource."""
    global remoteAccessNode
    global remoteAccessMethod
    global remoteCopyMethod
    global sshUserName
    global sshPassword
    global sshPassword4thisSession
    global sshPrivateKeyFile
    global sshPrivateKeyPassword

    successfully_connected = False
    passphrase_entrance_count = 0
    authorize_key_count = 0
    while True:
        # Try to connect
        str_io = io.StringIO()
        try:
            sys.stdout = sys.stderr = str_io
            cfg.sshAccess(remoteAccessNode, ssh=remoteAccessMethod, username=sshUserName, password=sshPassword,
                          PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=sshPrivateKeyPassword, logfile=str_io,
                          command='ls')

            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__

            successfully_connected = True
            break
        except Exception:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            response = str_io.getvalue()

            log.debug(
                "Had attempted to access resource without password and failed, below is resource response" +
                "=" * 80 +
                str_io.getvalue() +
                "=" * 80)

            # check if it asking for passphrase
            m = re.search(r"Enter passphrase for key '(.*)':", response)
            if m:
                if passphrase_entrance_count >= 3:
                    sshPrivateKeyPassword = None
                    sshPrivateKeyFile = None
                    break
                if passphrase_entrance_count > 0:
                    log.error("Incorrect passphrase try again")
                sshPrivateKeyFile = m.group(1)
                log.log_input("Enter passphrase for key '%s':" % sshPrivateKeyFile)
                sshPrivateKeyPassword = getpass.getpass("")
                passphrase_entrance_count += 1
                continue
            m2 = re.search(r"[pP]assword:", response)
            if m is None and sshPrivateKeyFile is not None and m2:
                log.warning(
                    "Can not login to head node. "
                    "Probably the public key of private key was not authorized on head node")
                log.info("Will try to add public key to list of authorized keys on head node")
                while True:
                    try:
                        authorize_key_count += 1
                        log.log_input("Enter password for %s@%s (will be used only during this session):" % (
                            sshUserName, remoteAccessNode))
                        sshPassword4thisSession = getpass.getpass("")
                        log.empty_line()
                        str_io = io.StringIO()
                        sys.stdout = sys.stderr = str_io
                        cfg.sshAccess(remoteAccessNode, ssh='ssh-copy-id', username=sshUserName,
                                      password=sshPassword4thisSession,
                                      PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=None, logfile=str_io,
                                      command='')

                        sys.stdout = sys.__stdout__
                        sys.stderr = sys.__stderr__
                        log.info(response)

                        log.info(
                            "Have added public key to list of authorized keys on head node, "
                            "will attempt to connect again.")
                        log.empty_line()
                        break
                    except Exception:
                        sys.stdout = sys.__stdout__
                        sys.stderr = sys.__stderr__
                        if verbose:
                            log.debug(
                                "Had attempted to add public key to list of authorized keys on head node and failed, " +
                                "below is resource response" +
                                "=" * 80 +
                                str_io.getvalue() +
                                "=" * 80)
                        log.error("Incorrect password try again.")
                        if authorize_key_count >= 3:
                            break
                if authorize_key_count < 3:
                    continue
            break
    return successfully_connected


def get_remote_access_method():
    global resource_name
    global remoteAccessNode
    global remoteAccessMethod
    global remoteCopyMethod
    global sshUserName
    global sshPassword
    global sshPassword4thisSession
    global sshPrivateKeyFile
    global sshPrivateKeyPassword
    global rsh
    global no_ping

    # set remoteAccessNode
    while True:
        log.log_input("Enter Resource head node (access node) full name (e.g. headnode.somewhere.org):")
        remoteAccessNode = input("[%s] " % resource_name)
        if remoteAccessNode.strip() == "":
            remoteAccessNode = resource_name

        response = os.system("ping -c 1 -w2 " + remoteAccessNode + " > /dev/null 2>&1")

        if response == 0:
            break
        else:
            if no_ping:
                log.warning("Can not ping %s, but asked to ignore it.", remoteAccessNode)
                break
            log.error("Incorrect head node name (can not ping %s), try again", remoteAccessNode)

    # set sshUserName
    current_user = getpass.getuser()
    ask_for_user_name = True

    while True:
        if ask_for_user_name:
            log.log_input("Enter username for resource access:")
            sshUserName = input("[%s] " % current_user)
            if sshUserName.strip() == "":
                sshUserName = current_user
            current_user = sshUserName

        # check password-less access
        if sshPassword is None:
            log.info("Checking for password-less access")
        else:
            log.info("Checking for resource access")
        successfully_connected = check_connection_to_resource()

        if successfully_connected:
            if sshPassword is None:
                log.info("Can access resource without password")
            else:
                log.info("Can access resource")

        if successfully_connected is False:
            log.info("Can not access resource without password")
            action_list = [("TryAgain", "The private and public keys was generated manually, right now. Try again.")]
            # check private keys
            user_home_dir = os.path.expanduser("~")
            private_keys = [
                os.path.join(user_home_dir, '.ssh', f[:-4]) for f in os.listdir(os.path.join(user_home_dir, '.ssh'))
                if os.path.isfile(os.path.join(user_home_dir, '.ssh', f))
                and f[-4:] == '.pub' and os.path.isfile(os.path.join(user_home_dir, '.ssh', f[:-4]))]

            if len(private_keys) > 0:
                action_list.append(("UseExistingPrivateKey", "Use existing private and public key."))

            action_list.append(("GenNewKey", "Generate new private and public key."))
            action_list.append(("UsePassword", "Use password directly."))
            log.empty_line()

            log.info(
                "Select authentication method:" +
                "\n".join(["%3d  %s" % (i, desc) for i, (_, desc) in enumerate(action_list)]))
            while True:
                log.log_input("Select option from list above:")
                try:
                    action = input("[2] ")
                    if action.strip() == "":
                        action = 2
                    else:
                        action = int(action)

                    if action < 0 or action >= len(action_list):
                        raise ValueError()
                    break
                except (ValueError, TypeError):
                    log.error("Incorrect entry, try again.")

            # do the action
            log.empty_line()
            if action_list[action][0] == "TryAgain":
                continue
            if action_list[action][0] == "UsePassword":
                log.log_input("Enter password for %s@%s:" % (sshUserName, remoteAccessNode))
                sshPassword = getpass.getpass("")
                ask_for_user_name = not ask_for_user_name
                continue
            if action_list[action][0] == "UseExistingPrivateKey":
                log.info("Available private keys:"+"\n".join(["%3d  %s" % (i, p) for i, p in enumerate(private_keys)]))
                while True:
                    log.log_input("Select key number from list above:")
                    try:
                        i_key = input("")
                        i_key = int(i_key)

                        if i_key < 0 or i_key >= len(private_keys):
                            raise ValueError()
                        break
                    except (ValueError, TypeError):
                        log.error("Incorrect entry, try again.")
                sshPrivateKeyFile = private_keys[i_key]
                ask_for_user_name = not ask_for_user_name
                continue
            if action_list[action][0] == "GenNewKey":
                count = 0
                while True:
                    log.log_input("Enter password for %s@%s (will be used only during this session):" % (
                        sshUserName, remoteAccessNode))
                    sshPassword4thisSession = getpass.getpass("")
                    sshPassword = sshPassword4thisSession

                    if check_connection_to_resource():
                        break
                    count += 1
                    if count >= 3:
                        break
                sshPassword = None
                # generate keys
                log.log_input("Enter private key name:")
                sshPrivateKeyFile = input("[id_rsa_%s]" % resource_name)
                if sshPrivateKeyFile.strip() == "":
                    sshPrivateKeyFile = "id_rsa_%s" % resource_name
                sshPrivateKeyFile = os.path.join(user_home_dir, '.ssh', sshPrivateKeyFile)
                log.log_input("Enter passphrase for new key (leave empty for passwordless access):")
                sshPrivateKeyPassword = getpass.getpass("")
                os.system("ssh-keygen -t rsa -N \"%s\" -f %s" % (sshPrivateKeyPassword, sshPrivateKeyFile))
                if sshPrivateKeyPassword.strip() == "":
                    sshPrivateKeyPassword = None
                # copy keys
                cfg.sshAccess(remoteAccessNode, ssh='ssh-copy-id', username=sshUserName,
                              password=sshPassword4thisSession,
                              PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=None, logfile=sys.stdout,
                              command='')
                ask_for_user_name = not ask_for_user_name
                continue

        if successfully_connected:
            break
        else:
            log.error("Incorrect resource access credential")

    if successfully_connected:
        log.empty_line()
        log.info("Connecting to " + resource_name)

        str_io = io.StringIO()
        try:
            sys.stdout = sys.stderr = str_io
            rsh = cfg.sshAccess(remoteAccessNode, ssh=remoteAccessMethod, username=sshUserName, password=sshPassword,
                                PrivateKeyFile=sshPrivateKeyFile, PrivateKeyPassword=sshPrivateKeyPassword,
                                logfile=sys.stdout,
                                command=None)
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
        except Exception as e:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            log.debug(str_io.getvalue())
            raise e

        log.info("              Done")
    log.empty_line()
    return successfully_connected


def get_system_characteristics():
    """detect system characteristics or ask user about them"""
    global ppn
    while True:
        try:
            log.log_input("Enter processors (cores) per node count:")
            ppn = int(input(""))
            break
        except (ValueError, TypeError):
            log.error("Incorrect entry, try again.")


def get_file_system_access_points():
    global resource_name
    global networkScratch
    global localScratch
    global akrrData
    global appKerDir

    home_dir = cfg.sshCommand(rsh, "echo $HOME").strip()
    scratch_network_dir = cfg.sshCommand(rsh, "echo $SCRATCH").strip()

    # localScratch
    local_scratch_default = "/tmp"
    while True:
        log.log_input("Enter location of local scratch (visible only to single node):")
        localScratch = input("[%s]" % local_scratch_default)
        if localScratch.strip() == "":
            localScratch = local_scratch_default
        status, msg = resource_deploy.check_dir_simple(rsh, localScratch)
        if status:
            log.info(msg)
            log.empty_line()
            break
        else:
            log.warning(msg)
            log.warning('local scratch might be have a different location on head node, so if it is by design it is ok')
            log.empty_line()
            break
    localScratch = cfg.sshCommand(rsh, "echo %s" % (localScratch,)).strip()
    # networkScratch
    network_scratch_default = ""
    if scratch_network_dir != "":
        network_scratch_default = scratch_network_dir
    network_scratch_visible = False
    while True:
        log.log_input(
            "Enter location of network scratch (visible only to all nodes),"
            "used for temporary storage of app kernel input/output:")
        if network_scratch_default != "":
            networkScratch = input("[%s]" % network_scratch_default)
            if networkScratch.strip() == "":
                networkScratch = network_scratch_default
        else:
            networkScratch = input("")

        if networkScratch == "":
            log.error("Incorrect value for networkScratch, try again")
            continue

        status, msg = resource_deploy.check_dir(rsh, networkScratch, exit_on_fail=False, try_to_create=True)
        if status:
            log.info(msg)
            network_scratch_visible = True
            log.empty_line()
            break
        else:
            log.warning(msg)
            break
    networkScratch = cfg.sshCommand(rsh, "echo %s" % (networkScratch,)).strip()
    # appKerDir
    appker_dir_default = os.path.join(home_dir, "appker", resource_name)
    while True:
        log.log_input("Enter future location of app kernels input and executable files:")
        appKerDir = input("[%s]" % appker_dir_default)
        if appKerDir.strip() == "":
            appKerDir = appker_dir_default
        status, msg = resource_deploy.check_dir(rsh, appKerDir, exit_on_fail=False, try_to_create=True)
        if status:
            log.info(msg)
            log.empty_line()
            break
        else:
            log.error(msg)
    appKerDir = cfg.sshCommand(rsh, "echo %s" % (appKerDir,)).strip()
    # akrrData
    akrr_data_default = os.path.join(home_dir, "akrr_data", resource_name)
    if network_scratch_visible:
        akrr_data_default = os.path.join(networkScratch, "akrr_data", resource_name)
    while True:
        log.log_input(
            "Enter future locations for app kernels working directories (can or even should be on scratch space):")
        akrrData = input("[%s]" % akrr_data_default)
        if akrrData.strip() == "":
            akrrData = akrr_data_default
        status, msg = resource_deploy.check_dir(rsh, akrrData, exit_on_fail=False, try_to_create=True)
        if status:
            log.info(msg)
            log.empty_line()
            break
        else:
            log.error(msg)
    akrrData = cfg.sshCommand(rsh, "echo %s" % (akrrData,)).strip()


def resource_add(config):
    """add resource, config should have following members
        dry_run - Dry Run No files will actually be created
        minimalistic - Minimize questions number, configuration files will be edited manually
        no-ping - do not run ping to test headnode name
        verbose
    """
    global verbose
    global dry_run
    global no_ping
    global minimalistic
    global resource_name
    global remoteAccessNode
    global remoteAccessMethod
    global remoteCopyMethod
    global sshUserName
    global sshPassword
    global sshPrivateKeyFile
    global sshPrivateKeyPassword
    global networkScratch
    global localScratch
    global akrrData
    global appKerDir
    global batchScheduler
    global batchJobHeaderTemplate

    if config.verbose:
        verbose = True

    log.info("Beginning Initiation of New Resource...")
    verbose = config.verbose
    dry_run = config.dry_run
    resource_deploy.dry_run = config.dry_run
    no_ping = config.no_ping
    minimalistic = config.minimalistic

    log.info("Retrieving Resources from XDMoD Database...")
    # RETRIEVE: the resources from XDMoD
    resources = retrieve_resources()
    log.info(
        "Found following resources from XDMoD Database:\n" +
        "    resource_id  name\n" +
        "\n".join(["    %11d  %-40s" % (resource_id, resource_name) for resource_name, resource_id in resources]) +
        "\n")

    if len(resources) > 0:
        while True:
            log.log_input('Enter resource_id for import (enter 0 for no match):')
            resource_id = input()
            if validate_resource_id(resource_id, resources):
                break
            log.warning("Incorrect resource_id try again")
        log.empty_line()
        resource_id = int(resource_id)
    else:
        resource_id = 0

    if resource_id <= 0:  # i.e. no match from XDMoD DB
        resource_id = None

    resource_name = ""
    while True:
        if resource_id is None:
            log.log_input('Enter AKRR resource name:')
            resource_name = input()
        else:
            resource_name2 = get_resource_name_by_id(resource_id, resources)
            log.log_input(
                'Enter AKRR resource name, hit enter to use same name as in XDMoD Database [%s]:' % (resource_name2,))
            resource_name = input()
            if resource_name.strip() == "":
                resource_name = resource_name2

        if validate_resource_name(resource_name):
            break
    log.empty_line()

    while True:
        log.log_input('Enter queuing system on resource (slurm or pbs): ')
        queuing_system = input()
        if validate_queuing_system(queuing_system):
            break
        else:
            log.error("Incorrect queuing_system try again")

    batchScheduler = queuing_system
    log.empty_line()

    if minimalistic is False:
        get_remote_access_method()
        get_system_characteristics()
        get_file_system_access_points()

    log.debug("Summary of parameters" +
              "resource_name: {}".format(resource_name) +
              "remoteAccessNode: {}".format(remoteAccessNode) +
              "remoteAccessMethod: {}".format(remoteAccessMethod) +
              "remoteCopyMethod: {}".format(remoteCopyMethod) +
              "sshUserName: {}".format(sshUserName) +
              "sshPassword: {}".format(sshPassword) +
              "sshPrivateKeyFile: {}".format(sshPrivateKeyFile) +
              "sshPrivateKeyPassword: {}".format(sshPrivateKeyPassword) +
              "networkScratch: {}".format(networkScratch) +
              "localScratch: {}".format(localScratch) +
              "akrrData: {}".format(akrrData) +
              "appKerDir: {}".format(appKerDir) +
              "batchScheduler: {}".format(batchScheduler) +
              "batchJobHeaderTemplate: {}".format(batchJobHeaderTemplate) + "\n")
    
    generate_resource_config(resource_id, resource_name, queuing_system)
    log.info(
        "Initiation of new resource is completed.\n"
        "    Edit batchJobHeaderTemplate variable in {}\n"
        "    and move to resource validation and deployment step.\n"
        "    i.e. execute:\n"
        "        akrr resource deploy -r {}".format(resource_cfg_filename, resource_name))
