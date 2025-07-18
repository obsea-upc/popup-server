# PLOME Pop-up Server #

This project provides an API that controls the release of pop-up buoys based on a Flask API.

This work has been funded by the PLOME project.

## Buoy status file ##
The `log/status.tab` logs the last operation performed by each PopUp buoy. the columns are buoy_id, time and status. The status codes are:
* `I`: initialized by the server, no interaction from popup buoy yet
* `S`: buoy time synchronized 
* `P`: Buoy asked for permission to be released
* `A`: Release attempt
* `R`: Successful release


### Contact info ###

* **authors**: Enoc Martínez, Matias Carandell 
* **version**: 0.0.1  
* **organization**: Universitat Politècnica de Catalunya (UPC)  
* **contact**: enoc.martinez@upc.edu  