import csv
import json
import logging
import subprocess
import sys

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)


def _query(dist_name):
    for query_name in [f'python3-{dist_name}', dist_name]:
        cmd = ['sudo', 'dnf', '--quiet', 'repoquery', '--queryformat',
               '%{name} %{version}', query_name]
        logger.debug(' '.join(cmd))
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        for line in completed.stdout.decode('utf-8').splitlines():
            yield line.split()


def do_find_rpms(args):
    with open(args.build_order_file, 'r') as f:
        build_order = json.load(f)

    if args.output:
        outfile = open(args.output, 'w')
    else:
        outfile = sys.stdout
    writer = csv.writer(outfile)
    writer.writerow(("Result", "Dist Name", "Dist Version", "RPM Name", "RPM Version"))

    def show(match, step, rpm_name='', rpm_version=''):
        writer.writerow((match, step['dist'], step['version'],
                         rpm_name, rpm_version))
        if args.output:
            outfile.flush()

    for step in build_order:
        rpm_info = list(_query(step['dist']))

        if not rpm_info:
            show('NO RPM', step)
            continue

        # Look first for a match. If we don't find one, report all of
        # the other mismatched versions (there may be multiples).
        others = []
        for entry in rpm_info:
            dist_version = Version(step['version'])

            try:
                rpm_name, rpm_version_str = entry
            except Exception as err:
                raise RuntimeError(f'Could not parse {entry}') from err
            try:
                rpm_version = Version(rpm_version_str)
            except InvalidVersion:
                # Some RPM versions can't be parsed as Python package
                # versions (tzdata). We can't safely compare the
                # strings, except for exact equality, so fall back to
                # saying the versions are different.
                rpm_version = rpm_version_str
                if step['version'] == rpm_version_str:
                    result = 'OK'
                else:
                    result = 'DIFF'

            else:
                if dist_version == rpm_version:
                    result = 'OK'
                elif rpm_version < dist_version:
                    result = 'OLD'
                else:
                    result = 'DIFF'

            if result == 'OK':
                show('OK', step, rpm_name, rpm_version)
                break
            else:
                others.append((result, step, rpm_name, rpm_version))
        else:
            for o in others:
                show(*o)

    if args.output:
        outfile.close()
