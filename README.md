# 从文件提交
python awd_submitter.py -f flags.txt -c targets.yaml

# 从管道提交
echo "flag{xxx}, flag{yyy}" | python awd_submitter.py -c targets.yaml

