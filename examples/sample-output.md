```text
examples/requirements-unsorted.txt:4: 'Django' is out of order — it should come before 'requests' (line 2)
examples/requirements-unsorted.txt:6: 'flask' is out of order — it should come before 'urllib3' (line 5)
examples/requirements-unsorted.txt:8: 'celery' is out of order — it should come before 'numpy' (line 7)
examples/configs/requirements.txt:2: 'awscli' is out of order — it should come before 'boto3' (line 1)
Checked 4 file(s), 4 problem(s) in 2 file(s).
```

Output of `sh examples/run-example.sh` — the checker on the whole
`examples/` folder. It exits 1, because the examples contain files
with entries out of order on purpose; the script itself exits 0.
