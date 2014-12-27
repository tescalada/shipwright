# -*- coding: utf-8 -*-
"""
Shipwright -- Builds shared Docker images within a common git repository.


Usage:
  shipwright [options] [build|push [--no-build]|purge]
             [TARGET]...
             [-d TARGET]...
             [-e TARGET]...
             [-u TARGET]...
             [-x TARGET]...
            

Options:

 --help           Show all help information

 -H DOCKER_HOST   Override DOCKER_HOST if it's set in the environment.

 
Specifiers:

  -d --dependents=TARGET  Build TARGET and all its dependents

  -e --exact=TARGET       Build TARGET only - may fail if
                          dependencies have not been built 

  -u --upto=TARGET        Build TARGET and it dependencies

  -x --exclude=TARGET     Build everything but TARGET and 
                          its dependents


Environment Variables:

  SW_NAMESPACE : If DOCKER_HUB_ACCOUNT is not passed on the command line
   this Environment variable must be present.

  DOCKER_HOST : Same URL as used by the docker client to connect to 
    the docker daemon. 

Examples:

  Assuming adependencies tree that looks like this.
  
  ubuntu
    └─── base
        └─── shared
        |     ├─── service1  
        |     |     └─── service2
        |     └─── service3
        └─── independent


  Build everything: 

    $ shipwright

  Build base, shared and service1:

    $ shipwright service1

  Build base, shared and service1, service2:

    $ shipwright -d service1

  Use exclude to build base, shared and service1, service2:

    $ shipwright -x service3 -x independent

  Build base, independent, shared and service3

    $ shipwright -x service1

  Build base, independent, shared and service1, service2:

    $ shipwright -d service1 -u independent

  Note that specfying a TARGET is the same as -u so the following
  command is equivalent to the one above.

  $ shipwright -d service1 independent


"""
from __future__ import absolute_import
from __future__ import print_function


import sys
import os
import json
from itertools import cycle,chain

from docopt import docopt
import docker
import git

from shipwright import Shipwright
from shipwright.version import version


from shipwright.dependencies import dependents, exact, exclude, upto
from shipwright.colors import rainbow
from shipwright.fn import _0
from shipwright import fn



# todo: only do this if python 2.7
import ssl



def main():
  arguments = docopt(__doc__, options_first=False, version='Shipwright ' + version)
  repo = git.Repo(os.getcwd())

  try:
    config = json.load(open(
      os.path.join(repo.working_dir, '.shipwright.json')
    ))
  except OSError:
    config = {
      'namespace': arguments['DOCKER_HUB_ACCOUNT'] or os.environ.get('SW_NAMESPACE')
    }



  if config['namespace'] is None:
    exit(
      "Please specify your docker hub account in\n"
      "the .shipwright.json config file,\n "
      "the command line or set SW_NAMESPACE.\n"
      "Run shipwright --help for more information."
    )

  
  base_url = os.environ.get('DOCKER_HOST','unix:///var/run/docker.sock')
  
  DOCKER_TLS_VERIFY = bool(os.environ.get('DOCKER_TLS_VERIFY', False))
 
  # todo: replace with from docker.utils import kwargs_from_env
  if not DOCKER_TLS_VERIFY:
    tls_config = False
  else:
    cert_path = os.environ.get('DOCKER_CERT_PATH')
    if cert_path:
      ca_cert_path = os.path.join(cert_path,'ca.pem')
      client_cert=(
        os.path.join(cert_path, 'cert.pem'), 
        os.path.join(cert_path, 'key.pem')
      )

    tls_config = docker.tls.TLSConfig(
      ssl_version = ssl.PROTOCOL_TLSv1,
      client_cert = client_cert,
      verify=ca_cert_path,
      assert_hostname=False
    )
    if base_url.startswith('tcp://'):
      base_url = 'https://' + base_url[6:]

  client = docker.Client(
    base_url=base_url,
    version='1.12',
    timeout=10,
    tls=tls_config
  )

  # specifiers = chain(
  #   [exact(t) for t in arguments.pop('--exact')],
  #   [dependents(t) for t in arguments.pop('--dependents')],
  #   [exclude(t) for t in arguments.pop('--exclude')],
  #   [upto(t) for t in arguments.pop('--upto')],
  #   [upto(t) for t in arguments.pop('TARGET')]
  # )

  specifiers = chain(
    map(exact, arguments.pop('--exact')),
    map(dependents, arguments.pop('--dependents')),
    map(exclude, arguments.pop('--exclude')),
    map(upto, arguments.pop('--upto')),
    map(upto, arguments.pop('TARGET'))
  )


  # {'publish': false, 'purge': true, ...} = 'purge'
  command_name = _0([
    command for (command, enabled) in arguments.items()
    if command.islower() and enabled
  ]) or "build"

  command = getattr(Shipwright(config,repo,client), command_name)


  for event in command(specifiers):
    show_fn = mk_show(event)
    show_fn(switch(event))

def exit(msg):
  print(msg)
  sys.exit(1)
 
def memo(f, arg, memos={}):
  if arg in memos:
    return memos[arg]
  else:
    memos[arg] = f(arg)
    return memos[arg]

def mk_show(evt):
  if evt['event'] in ('build_msg', 'push') or 'error' in evt:
    return memo(
      highlight, 
      fn.maybe(fn.getattr('name'), evt.get('container'))
      or evt.get('image')
      )
  else:
    return print

colors = cycle(rainbow())
def highlight(name):
  color_fn = next(colors)
  def highlight_(msg):
    print(color_fn(name) + " | " + msg)
  return highlight_

def switch(rec):

  if 'stream' in rec:
    return rec['stream'].strip('\n')

  elif 'status' in rec:
    if rec['status'].startswith('Downloading'):
      term = '\r'
    else:
      term = ''

    return '[STATUS] {0}: {1}{2}'.format(
      rec.get('id', ''), 
      rec['status'],
      term
    )
  elif 'error' in rec:
    return '[ERROR] {0}\n'.format(rec['errorDetail']['message'])
  elif rec['event'] == 'tag':
    return 'Tagging {image} to {name}:{tag}'.format(name=rec['container'].name, **rec)
  elif rec['event'] == 'removed':
    return 'Untagging {image}:{tag}'.format(**rec)
  else:
    return rec
