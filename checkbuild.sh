#!/usr/bin/env bash

# Get PR number from GITHUB_REF (refs/pull/#/merge)
PR=${GITHUB_REF#"refs/pull/"}
PR=${PR%"/merge"}

echo "PR: $PR"

GITHUB_URL=https://api.github.com/repos/${GITHUB_REPOSITORY}/issues/${PR}/comments

MSG=

function post_msg()
{
	echo "Post message to github"
	echo "URL: {$GITHUB_URL}"
	echo "MSG: {$MSG}"

	curl ${GITHUB_URL} \
		-H "Authorization: token ${GITHUB_TOKEN}" \
		-H "Content-Type: application/json" \
		-X POST --data "$(cat <<EOF
{
	"body": "${MSG}"
}
EOF
)"
}

function post_build_success()
{
	MSG="Checkbuild passed"
	post_msg
}

function post_build_fail()
{
	MSG="Checkbuild failed"
	post_msg
}

# For future use: In case to save the output to the file
# { ./bootstrap-configure && make && echo "0" > build_status; } 2>&1 | tee build_output.log

echo "#######################"
echo "##### Build Start #####"
echo "#######################"
./bootstrap-configure --enable-external-ell && make

RESULT=$?

if [[ "$RESULT" == "0" ]]; then
	echo "Build Success"
	post_build_success
else
	echo "Build Failed"
	post_build_fail
fi

exit $RESULT
