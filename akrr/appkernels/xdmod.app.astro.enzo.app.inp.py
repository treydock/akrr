walllimit=240

parser="xdmod.app.astro.enzo.py"

#path to run script relative to AppKerDir on particular resource
#execs/enzo/yt-x86_64/src/enzo-hg-stable/src/enzo/enzo.exe
executable="execs/enzo/yt-x86_64/src/enzo-hg-stable"
input="inputs/enzo/ReionizationRadHydro128"

runScriptPreRun="""#create working dir
export AKRR_TMP_WORKDIR=`mktemp -d {networkScratch}/namd.XXXXXXXXX`
echo "Temporary working directory: $AKRR_TMP_WORKDIR"
cd $AKRR_TMP_WORKDIR

#Copy inputs
cp {appKerDir}/{input}/* ./

writeToGenInfo "input" "{input}"
"""

runScriptPostRun="""#clean-up
cd $AKRR_TASK_WORKDIR
if [ "${{AKRR_DEBUG=no}}" = "no" ]
then
        echo "Deleting temporary files"
        rm -rf $AKRR_TMP_WORKDIR
else
        echo "Copying temporary files"
        cp -r $AKRR_TMP_WORKDIR workdir
        rm -rf $AKRR_TMP_WORKDIR
fi
"""


