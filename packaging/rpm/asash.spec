%global debug_package %{nil}
%global __brp_mangle_shebangs %{nil}
%global __brp_python_bytecompile %{nil}
%global __requires_exclude_from ^/opt/venvs/asash/.*$
%global __provides_exclude_from ^/opt/venvs/asash/.*$

Name:           asash
Version:        %{_version}
Release:        1%{?dist}
Summary:        Static analysis of shell programs using symbolic execution
License:        MIT
URL:            https://github.com/atlas-brown/sash
Conflicts:      sash
BuildRequires:  python3 >= 3.10
BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  make
BuildRequires:  autoconf
BuildRequires:  automake
BuildRequires:  libtool
Requires:       python3 >= 3.10

%description
SaSh analyzes POSIX shell programs ahead of time, using symbolic execution
to predict their effects on the file system and to find bugs before the
program is ever run.

For example, it reports word splitting or empty variables that could lead
to deletion of critical paths such as /*, and commands that could silently
overwrite files, e.g. two mv commands to the same destination.

SaSh is the artifact of the SOSP'26 paper "Ahead-of-time Analysis of Shell
Program Effects". Its Python dependencies (libdash, shasta, z3,
pash-annotations) are bundled in a private virtualenv under
/opt/venvs/asash.

%build

export CFLAGS="-std=gnu17 ${CFLAGS:-}"
python3 -m venv "%{_builddir}/venv"

"%{_builddir}/venv/bin/pip" install --no-index --find-links "%{srcdir}/vendor" \
    --no-deps setuptools "wheel<0.38" uv_build
"%{_builddir}/venv/bin/pip" install --no-index --find-links "%{srcdir}/vendor" \
    --no-build-isolation --no-deps -r "%{srcdir}/vendor/requirements.txt"

PATH="%{_builddir}/venv/bin:$PATH" "%{_builddir}/venv/bin/pip" install \
    --no-index --find-links "%{srcdir}/vendor" \
    --no-build-isolation --no-deps "%{srcdir}"

%install
rm -rf "%{buildroot}"
mkdir -p "%{buildroot}/opt/venvs" "%{buildroot}/usr/bin"
cp -a "%{_builddir}/venv" "%{buildroot}/opt/venvs/asash"

for f in "%{buildroot}/opt/venvs/asash/bin/"*; do
    [ -f "$f" ] || continue
    sed -i "1s|^#!.*/venv/bin/python.*|#!/opt/venvs/asash/bin/python3|" "$f"
done
sed -i "s|^home = .*|home = /usr/bin|" "%{buildroot}/opt/venvs/asash/pyvenv.cfg"

ln -s /opt/venvs/asash/bin/sash "%{buildroot}/usr/bin/sash"

%files
/opt/venvs/asash
/usr/bin/sash

%changelog
* Thu Jan 01 2026 SaSh maintainers <atlas@brown.edu> - 0.1.0-1
- Initial packaging.
